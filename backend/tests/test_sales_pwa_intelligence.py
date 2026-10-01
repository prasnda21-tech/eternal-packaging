"""Sales intelligence feed: session scope, explainable priority, no second prediction formula."""
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

JUNE = datetime(2026, 6, 1, 3, 0, tzinfo=timezone.utc)
WIB_MIDNIGHT = datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)


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


def _user(uid, name="Sales A"):
    return {
        "id": uid,
        "user_id": uid,
        "email": f"{uid}@eternal.test",
        "name": name,
        "role": "sales_eternal",
        "permissions": list(ROLE_DEFAULT_PERMISSIONS["sales_eternal"]),
    }


def _order(uid, customer, day, *, customer_id=None, name=None, quantity=1000, price=1000, **extra):
    doc = {
        "id": str(uuid.uuid4()),
        "po_number": extra.pop("po_number", f"PO-I-{uuid.uuid4().hex[:8]}"),
        "customer": customer,
        "customer_name": customer,
        "sales_user_id": uid,
        "sales": name or "Sales A",
        "order_date": day,
        "cancelled": extra.pop("cancelled", False),
        "line_items": extra.pop("line_items", [{"name": "Cup", "quantity": quantity, "unit_price": price, "unit": "pcs"}]),
        "created_at": f"{day}T00:00:00+07:00",
    }
    if customer_id is not None:
        doc["customer_id"] = customer_id
    doc.update(extra)
    return doc


async def _feed(db, user, now=JUNE):
    from services.sales_pwa_intelligence import build_sales_pwa_intelligence
    return await build_sales_pwa_intelligence(db, user, now=now)


def _by_name(payload, name):
    return [item for item in payload["items"] if item["customer_name"] == name]


@pytest.mark.anyio
async def test_session_scope_and_spoof_are_ignored():
    import server
    db = server.db
    sales_a = f"intel-a-{uuid.uuid4().hex[:8]}"
    sales_b = f"intel-b-{uuid.uuid4().hex[:8]}"
    marker = uuid.uuid4().hex[:8]
    days = ("2026-05-01", "2026-05-08", "2026-05-15")
    await db.orders.insert_many(
        [_order(sales_a, f"Toko A {marker}", day, customer_id=f"a-{marker}") for day in days]
        + [_order(sales_b, f"Toko B {marker}", day, customer_id=f"b-{marker}") for day in days]
    )
    app.dependency_overrides[current_user] = lambda: _user(sales_a)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        listed = await client.get("/api/sales-pwa/intelligence", params={
            "sales_user_id": sales_b,
            "sales_id": sales_b,
            "user_id": sales_b,
        })
        assert listed.status_code == 200, listed.text
        body = listed.text
        assert f"Toko B {marker}" not in body
        assert f"b-{marker}" not in body
        assert f"Toko A {marker}" in body
        detail = await client.get(f"/api/sales-pwa/predictions/id%3Ab-{marker}")
        assert detail.status_code == 404
    await db.orders.delete_many({"sales_user_id": {"$in": [sales_a, sales_b]}})


@pytest.mark.anyio
async def test_overdue_today_and_future_follow_up():
    import server
    from services.order_prediction import wib_today
    db = server.db
    sales_a = f"intel-fu-{uuid.uuid4().hex[:8]}"
    sales_b = f"intel-fu-b-{uuid.uuid4().hex[:8]}"
    today = wib_today()
    previous = today.replace(day=1) if today.day > 1 else today.replace(month=today.month - 1 if today.month > 1 else 12, day=28)
    from datetime import timedelta
    previous = today - timedelta(days=4)
    future = today + timedelta(days=3)
    outlets = {
        "overdue": str(uuid.uuid4()),
        "today": str(uuid.uuid4()),
        "future": str(uuid.uuid4()),
        "other": str(uuid.uuid4()),
        "done": str(uuid.uuid4()),
    }
    await db.sales_outlets.insert_many([
        {"id": outlets["overdue"], "name": "Outlet Telat"},
        {"id": outlets["today"], "name": "Outlet Hari Ini"},
        {"id": outlets["future"], "name": "Outlet Nanti"},
        {"id": outlets["other"], "name": "Outlet Orang Lain"},
        {"id": outlets["done"], "name": "Outlet Selesai"},
    ])
    await db.sales_ownership.insert_many([
        {"outlet_id": outlets["overdue"], "current_owner_id": sales_a, "next_follow_up_at": f"{previous.isoformat()}T09:00:00+07:00"},
        {"outlet_id": outlets["today"], "current_owner_id": sales_a, "next_follow_up_at": f"{today.isoformat()}T09:00:00+07:00"},
        {"outlet_id": outlets["future"], "current_owner_id": sales_a, "next_follow_up_at": f"{future.isoformat()}T09:00:00+07:00"},
        {"outlet_id": outlets["other"], "current_owner_id": sales_b, "next_follow_up_at": f"{previous.isoformat()}T09:00:00+07:00"},
        {"outlet_id": outlets["done"], "current_owner_id": sales_a, "next_follow_up_at": None},
    ])
    await db.sales_activities.insert_one({
        "id": str(uuid.uuid4()),
        "outlet_id": outlets["done"],
        "sales_user_id": sales_a,
        "activity_type": "visit",
        "activity_at": f"{previous.isoformat()}T08:00:00+07:00",
        "result": "Selesai",
        "next_follow_up_at": None,
    })
    payload = await _feed(db, _user(sales_a), now=datetime.now(timezone.utc))
    names = [item["customer_name"] for item in payload["items"]]
    assert "Outlet Telat" in names
    assert "Outlet Hari Ini" not in names
    assert "Outlet Nanti" not in names
    assert "Outlet Orang Lain" not in names
    assert "Outlet Selesai" not in names
    late = next(item for item in payload["items"] if item["customer_name"] == "Outlet Telat")
    assert late["type"] == "overdue_follow_up"
    assert late["days"] == 4
    assert late["reason"] == "Follow-up terlambat 4 hari"
    assert late["action"] == {"kind": "visit", "href": "/sales/visit"}
    assert payload["counts"]["overdue_follow_up"] == 1
    await db.sales_ownership.delete_many({"outlet_id": {"$in": list(outlets.values())}})
    await db.sales_outlets.delete_many({"id": {"$in": list(outlets.values())}})
    await db.sales_activities.delete_many({"outlet_id": {"$in": list(outlets.values())}})


@pytest.mark.anyio
async def test_wib_midnight_boundary_makes_previous_day_overdue():
    import server
    db = server.db
    sales_a = f"intel-wib-{uuid.uuid4().hex[:8]}"
    late_id = str(uuid.uuid4())
    today_id = str(uuid.uuid4())
    await db.sales_outlets.insert_many([
        {"id": late_id, "name": "Batas Kemarin"},
        {"id": today_id, "name": "Batas Hari Ini"},
    ])
    await db.sales_ownership.insert_many([
        {"outlet_id": late_id, "current_owner_id": sales_a, "next_follow_up_at": "2026-09-30T23:00:00+07:00"},
        {"outlet_id": today_id, "current_owner_id": sales_a, "next_follow_up_at": "2026-10-01T00:30:00+07:00"},
    ])
    payload = await _feed(db, _user(sales_a), now=WIB_MIDNIGHT)
    assert payload["as_of"] == "2026-10-01"
    names = [item["customer_name"] for item in payload["items"]]
    assert names == ["Batas Kemarin"]
    assert payload["items"][0]["reason"] == "Follow-up terlambat 1 hari"
    await db.sales_ownership.delete_many({"outlet_id": {"$in": [late_id, today_id]}})
    await db.sales_outlets.delete_many({"id": {"$in": [late_id, today_id]}})


@pytest.mark.anyio
async def test_inactive_uses_prediction_status_and_short_history_is_not_stale():
    import server
    db = server.db
    sales_a = f"intel-stale-{uuid.uuid4().hex[:8]}"
    cid = f"stale-{uuid.uuid4().hex[:6]}"
    thin = f"thin-{uuid.uuid4().hex[:6]}"
    docs = [
        _order(sales_a, "Toko Diam", day, customer_id=cid)
        for day in ("2026-01-01", "2026-01-11", "2026-01-21")
    ]
    docs.append(_order(sales_a, "Toko Baru", "2025-01-01", customer_id=thin))
    docs.append(_order(sales_a, "Toko Baru", "2025-08-01", customer_id=thin))
    await db.orders.insert_many(docs)
    payload = await _feed(db, _user(sales_a))
    stale = _by_name(payload, "Toko Diam")
    assert len(stale) == 1
    assert stale[0]["type"] == "inactive"
    assert stale[0]["prediction_status"] == "TIDAK AKTIF"
    assert stale[0]["reason"] == "131 hari sejak order terakhir, melewati batas tidak aktif"
    assert _by_name(payload, "Toko Baru") == []
    assert payload["counts"]["inactive"] == 1
    assert payload["counts"]["insufficient_data"] >= 1
    await db.orders.delete_many({"sales_user_id": sales_a})


@pytest.mark.anyio
async def test_frequency_qty_and_value_decline_rules():
    import server
    db = server.db
    sales_a = f"intel-dec-{uuid.uuid4().hex[:8]}"
    positive = f"pos-{uuid.uuid4().hex[:6]}"
    negative = f"neg-{uuid.uuid4().hex[:6]}"
    qty_id = f"qty-{uuid.uuid4().hex[:6]}"
    missing_qty = f"mq-{uuid.uuid4().hex[:6]}"
    value_id = f"val-{uuid.uuid4().hex[:6]}"
    missing_price = f"mp-{uuid.uuid4().hex[:6]}"
    zero_old = f"zero-{uuid.uuid4().hex[:6]}"
    docs = []
    for day in ("2026-01-01", "2026-01-11", "2026-02-10", "2026-03-02"):
        docs.append(_order(sales_a, "Toko Melambat", day, customer_id=positive))
    for day in ("2026-01-01", "2026-01-11", "2026-02-01", "2026-02-13"):
        docs.append(_order(sales_a, "Toko Stabil", day, customer_id=negative))
    qty_days = ("2026-01-01", "2026-01-11", "2026-02-01", "2026-02-13")
    qty_amounts = (1000, 1000, 400, 400)
    for day, amount in zip(qty_days, qty_amounts):
        docs.append(_order(sales_a, "Toko Qty", day, customer_id=qty_id, quantity=amount, price=1000))
    for index, day in enumerate(qty_days):
        lines = [{"name": "Cup", "quantity": 1000, "unit_price": 1000}]
        if index == 3:
            lines = [{"name": "Cup", "unit_price": 1000}]
        docs.append(_order(sales_a, "Toko Qty Kosong", day, customer_id=missing_qty, line_items=lines))
    values = (1000, 1000, 200, 200)
    for day, price in zip(qty_days, values):
        docs.append(_order(sales_a, "Toko Nilai", day, customer_id=value_id, quantity=10, price=price))
    for day in qty_days:
        docs.append(_order(sales_a, "Toko Harga Kosong", day, customer_id=missing_price, line_items=[{"name": "Cup", "quantity": 500}]))
    for day in ("2026-01-01", "2026-01-01", "2026-02-01", "2026-02-15"):
        docs.append(_order(sales_a, "Toko Nol", day, customer_id=zero_old, quantity=100, price=10))
    await db.orders.insert_many(docs)
    early = await _feed(db, _user(sales_a), now=datetime(2026, 2, 14, 3, 0, tzinfo=timezone.utc))
    later = await _feed(db, _user(sales_a), now=datetime(2026, 3, 3, 3, 0, tzinfo=timezone.utc))
    slow = _by_name(later, "Toko Melambat")
    assert len(slow) == 1
    assert slow[0]["type"] == "declining"
    assert slow[0]["reason"] == "Jarak order memanjang dari 10 hari menjadi 20 hari"
    stable = _by_name(later, "Toko Stabil")
    assert stable and stable[0]["type"] != "declining"
    assert "memanjang" not in stable[0]["reason"]
    assert _by_name(early, "Toko Qty Kosong") == []
    assert _by_name(early, "Toko Harga Kosong") == []
    assert all("Jarak order" not in item["reason"] for item in _by_name(early, "Toko Nol"))
    qty = _by_name(early, "Toko Qty")
    assert len(qty) == 1
    assert qty[0]["type"] == "declining"
    assert qty[0]["reason"] == "Jumlah barang turun dari 2.000 menjadi 800"
    valued = _by_name(early, "Toko Nilai")
    assert len(valued) == 1
    assert valued[0]["reason"] == "Nilai order turun dari Rp20.000 menjadi Rp4.000"
    await db.orders.delete_many({"sales_user_id": sales_a})


@pytest.mark.anyio
async def test_duplicate_categories_keep_one_higher_row():
    import server
    db = server.db
    sales_a = f"intel-dup-{uuid.uuid4().hex[:8]}"
    cid = f"dup-{uuid.uuid4().hex[:6]}"
    docs = [
        _order(sales_a, "Toko Ganda", day, customer_id=cid)
        for day in ("2026-03-01", "2026-03-08", "2026-04-20", "2026-05-10")
    ]
    outlet_id = str(uuid.uuid4())
    await db.orders.insert_many(docs)
    await db.sales_outlets.insert_one({"id": outlet_id, "name": "Outlet Ganda", "linked_customer_id": cid})
    await db.sales_ownership.insert_one({
        "outlet_id": outlet_id,
        "current_owner_id": sales_a,
        "next_follow_up_at": "2026-05-01T09:00:00+07:00",
    })
    payload = await _feed(db, _user(sales_a))
    rows = _by_name(payload, "Toko Ganda")
    assert len(rows) == 1
    assert rows[0]["type"] == "overdue_follow_up"
    assert rows[0]["customer_key"] == f"id:{cid}"
    assert rows[0]["action"]["kind"] == "visit"
    assert any("Jarak order" in reason or "periode order" in reason for reason in rows[0]["secondary_reasons"])
    assert payload["counts"]["priority_total"] == 1
    await db.orders.delete_many({"sales_user_id": sales_a})
    await db.sales_outlets.delete_many({"id": outlet_id})
    await db.sales_ownership.delete_many({"outlet_id": outlet_id})


@pytest.mark.anyio
async def test_name_only_and_ambiguous_stay_separate_and_read_only():
    import server
    db = server.db
    sales_a = f"intel-id-{uuid.uuid4().hex[:8]}"
    left = f"left-{uuid.uuid4().hex[:6]}"
    right = f"right-{uuid.uuid4().hex[:6]}"
    docs = []
    for day in ("2026-05-01", "2026-05-08", "2026-05-15"):
        docs.append(_order(sales_a, "Toko Kembar", day, customer_id=left))
    docs.append(_order(sales_a, "Toko Kembar", "2026-04-01", customer_id=right))
    for day in ("2026-01-01", "2026-01-11", "2026-01-21"):
        docs.append(_order(sales_a, "Toko Kembar", day, customer_id=None))
    docs.append(_order(sales_a, "Nama Saja", "2026-05-01", customer_id=None))
    docs.append(_order(sales_a, "Nama Saja", "2026-05-08", customer_id=None))
    docs.append(_order(sales_a, "Nama Saja", "2026-05-15", customer_id=None))
    await db.orders.insert_many(docs)
    payload = await _feed(db, _user(sales_a))
    keys = [item["customer_key"] for item in payload["items"]]
    assert f"id:{left}" in keys
    assert f"id:{right}" not in keys
    assert "ambiguous:toko kembar" in keys
    assert "name:nama saja" in keys
    named = next(item for item in payload["items"] if item["customer_key"] == "name:nama saja")
    assert named["identity"] == "name"
    assert named["action"]["kind"] == "prediction"
    assert named["action"]["href"] == "/sales/prediksi/name%3Anama%20saja"
    assert "Tambahkan ke Agenda" not in payload.__repr__()
    ambiguous = next(item for item in payload["items"] if item["customer_key"] == "ambiguous:toko kembar")
    stable = next(item for item in payload["items"] if item["customer_key"] == f"id:{left}")
    assert ambiguous["identity"] == "ambiguous"
    assert ambiguous["last_order_date"] == "2026-01-21"
    assert stable["last_order_date"] == "2026-05-15"
    assert stable["action"]["kind"] == "prediction"
    await db.orders.delete_many({"sales_user_id": sales_a})


@pytest.mark.anyio
async def test_same_name_across_sales_does_not_leak():
    import server
    db = server.db
    sales_a = f"intel-cross-{uuid.uuid4().hex[:8]}"
    sales_b = f"intel-cross-b-{uuid.uuid4().hex[:8]}"
    docs = [
        _order(sales_a, "Toko Sama", day, customer_id="same-a")
        for day in ("2026-05-01", "2026-05-08", "2026-05-15")
    ]
    docs.append(_order(sales_b, "Toko Sama", "2024-02-02", customer_id="same-b"))
    await db.orders.insert_many(docs)
    payload = await _feed(db, _user(sales_a))
    rows = _by_name(payload, "Toko Sama")
    assert len(rows) == 1
    assert rows[0]["customer_key"] == "id:same-a"
    assert rows[0]["last_order_date"] == "2026-05-15"
    assert "same-b" not in str(payload)
    await db.orders.delete_many({"sales_user_id": {"$in": [sales_a, sales_b]}})


@pytest.mark.anyio
async def test_cancelled_and_demo_orders_are_ignored():
    import server
    db = server.db
    sales_a = f"intel-skip-{uuid.uuid4().hex[:8]}"
    cid = f"skip-{uuid.uuid4().hex[:6]}"
    docs = [
        _order(sales_a, "Toko Batal", "2026-01-01", customer_id=cid),
        _order(sales_a, "Toko Batal", "2026-01-11", customer_id=cid),
        _order(sales_a, "Toko Batal", "2026-01-21", customer_id=cid, cancelled=True),
        _order(sales_a, "Toko Demo", "2026-01-01", customer_id=f"demo-{cid}", is_demo=True),
        _order(sales_a, "Toko Demo", "2026-01-11", customer_id=f"demo-{cid}", is_test=True),
        _order(sales_a, "Toko Demo", "2026-01-21", customer_id=f"demo-{cid}", po_number="TEST-PO-1"),
    ]
    await db.orders.insert_many(docs)
    payload = await _feed(db, _user(sales_a))
    assert _by_name(payload, "Toko Batal") == []
    assert _by_name(payload, "Toko Demo") == []
    await db.orders.delete_many({"sales_user_id": sales_a})


@pytest.mark.anyio
async def test_incomplete_flag_when_cap_is_exceeded():
    import server
    import services.sales_pwa_intelligence as intel
    db = server.db
    sales_a = f"intel-cap-{uuid.uuid4().hex[:8]}"
    saved = dict(intel.INTELLIGENCE_CAPS)
    intel.INTELLIGENCE_CAPS["orders"] = 1
    intel.INTELLIGENCE_CAPS["prediction_rows"] = 1
    try:
        await db.orders.insert_many([
            _order(sales_a, "Toko Satu", "2026-05-01", customer_id="cap-1"),
            _order(sales_a, "Toko Dua", "2026-05-02", customer_id="cap-2"),
        ])
        payload = await _feed(db, _user(sales_a))
        assert payload["incomplete"] is True
        assert "orders" in payload["incomplete_reasons"]
        intel.INTELLIGENCE_CAPS["orders"] = saved["orders"]
        payload = await _feed(db, _user(sales_a))
        assert payload["incomplete"] is True
        assert "prediction_rows" in payload["incomplete_reasons"]
        assert "orders" not in payload["incomplete_reasons"]
    finally:
        intel.INTELLIGENCE_CAPS.clear()
        intel.INTELLIGENCE_CAPS.update(saved)
        await db.orders.delete_many({"sales_user_id": sales_a})


@pytest.mark.anyio
async def test_priority_order_puts_overdue_before_prediction_rows():
    import server
    db = server.db
    sales_a = f"intel-ord-{uuid.uuid4().hex[:8]}"
    risk = f"risk-{uuid.uuid4().hex[:6]}"
    inactive = f"off-{uuid.uuid4().hex[:6]}"
    docs = [_order(sales_a, "Zebra Risiko", day, customer_id=risk) for day in ("2026-05-01", "2026-05-08", "2026-05-15")]
    docs += [_order(sales_a, "Alpha Diam", day, customer_id=inactive) for day in ("2026-01-01", "2026-01-11", "2026-01-21")]
    outlet_id = str(uuid.uuid4())
    await db.orders.insert_many(docs)
    await db.sales_outlets.insert_one({"id": outlet_id, "name": "Outlet Urut"})
    await db.sales_ownership.insert_one({
        "outlet_id": outlet_id,
        "current_owner_id": sales_a,
        "next_follow_up_at": "2026-05-20T09:00:00+07:00",
    })
    payload = await _feed(db, _user(sales_a))
    types = [item["type"] for item in payload["items"]]
    assert types == ["overdue_follow_up", "risiko", "inactive"]
    risk_row = next(item for item in payload["items"] if item["type"] == "risiko")
    assert risk_row["reason"].startswith("7 hari melewati perkiraan order")
    assert "Customer rutin" in risk_row["reason"]
    await db.orders.delete_many({"sales_user_id": sales_a})
    await db.sales_outlets.delete_many({"id": outlet_id})
    await db.sales_ownership.delete_many({"outlet_id": outlet_id})
