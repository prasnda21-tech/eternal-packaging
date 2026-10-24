"""Qty Rencana Pengiriman widget. Isolated fixtures. No production data."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_delivery_qty_widget_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.dashboard_today import wib_today
from services.delivery_plan import UNIT_MIXED, UNIT_NONE, aggregate_delivery_plan_qty
from services.finance_widget_layout import LayoutError, default_widgets, finance_widget_allowed, validate_payload
from services.production_widget_layout import PRODUCTION_SPEC
from services.tanggal_pengiriman import calendar_date_to_wib_iso

pytestmark = pytest.mark.anyio

TODAY = "2026-10-24"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture
async def clean_db(bind_motor):
    await server.db.orders.delete_many({})
    await server.db.app_settings.delete_many({})
    yield
    await server.db.orders.delete_many({})
    await server.db.app_settings.delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _po(**extra):
    base = {
        "id": "ord-qty-1",
        "po_number": "PO/QTY/001",
        "customer_name": "PT Uji",
        "cancelled": False,
        "buffer_stock": False,
        "current_stage": 8,
        "delivery_status": "Belum Terkirim",
        "qty_delivered": 0,
        "total_quantity": 100,
        "line_items": [{"name": "Cup", "quantity": 100, "unit": "pcs", "production_process": "Cetak"}],
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": "2026-10-21T10:00:00+07:00",
        "payment_approved_at": "2026-10-21T11:00:00+07:00",
        "deadline": "2026-10-01",
    }
    base.update(extra)
    return base


def _series(body, unit):
    return next(item for item in body["series"] if item["unit"] == unit)


def _user(role="widget_reader", override=None):
    return {
        "id": f"u-{uuid.uuid4().hex[:8]}",
        "email": "qty-widget@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": "u-qty",
        "permissions_override": override or {},
    }


def test_one_po_and_same_day_sum_use_remaining_qty():
    body = aggregate_delivery_plan_qty(
        [
            _po(id="a", scheduled_delivery_date=TODAY, qty_delivered=0),
            _po(id="b", po_number="PO/QTY/002", scheduled_delivery_date=TODAY, line_items=[
                {"name": "Cup", "quantity": 20, "unit": "pcs", "production_process": "Cetak"},
            ], total_quantity=20),
        ],
        lead_time_days=3,
        today=TODAY,
        days=7,
    )
    pcs = _series(body, "PCS")
    assert pcs["today_qty"] == 120
    assert pcs["today_po_count"] == 2
    assert pcs["overdue_qty"] == 0
    assert pcs["overdue_po_count"] == 0
    assert pcs["points"][0] == {"date": TODAY, "qty": 120, "po_count": 2}
    assert len(pcs["points"]) == 7
    assert all("po_count" in point for point in pcs["points"])
    assert body["end"] == "2026-10-30"
    assert "realisasi" not in body


def test_partial_multi_sj_does_not_double_count():
    partial = _po(
        id="partial",
        scheduled_delivery_date=TODAY,
        qty_delivered=40,
        delivery_status="Sebagian Terkirim",
        delivery_confirmations=[{"id": "sj-1", "qty": 30}, {"id": "sj-2", "qty": 30}],
        shipments=[{"id": "sj-1", "qty": 30}, {"id": "sj-2", "qty": 30}],
    )
    naive = _po(
        id="naive",
        po_number="PO/QTY/NAIVE",
        scheduled_delivery_date=TODAY,
        qty_delivered=None,
        delivery_confirmations=[{"id": "sj-3", "qty": 25}, {"id": "sj-4", "qty": 25}],
    )
    duplicate = _po(id="partial", po_number="PO/QTY/DUP", scheduled_delivery_date=TODAY, qty_delivered=0)
    body = aggregate_delivery_plan_qty([partial, duplicate, naive], lead_time_days=3, today=TODAY, days=7)
    assert _series(body, "PCS")["today_qty"] == 160


def test_excluded_rows_and_buffer_and_dates():
    yesterday = "2026-10-23"
    day_six = "2026-10-30"
    day_seven = "2026-10-31"
    orders = [
        _po(id="full", scheduled_delivery_date=TODAY, qty_delivered=100, delivery_status="Terkirim", current_stage=14),
        _po(id="cancel", cancelled=True, scheduled_delivery_date=TODAY),
        _po(id="bad-date", delivery_deadline_manual="bukan-tanggal", design_approved_at=None, payment_approved_at=None, deadline=TODAY),
        _po(id="past", scheduled_delivery_date=yesterday, line_items=[{"name": "Cup", "quantity": 999, "unit": "pcs"}]),
        _po(
            id="buffer-past",
            buffer_stock=True,
            scheduled_delivery_date=yesterday,
            line_items=[{"name": "Cup", "quantity": 40, "unit": "pcs"}],
            total_quantity=40,
        ),
        _po(id="buffer", buffer_stock=True, scheduled_delivery_date=TODAY, line_items=[{"name": "Cup", "quantity": 8, "unit": "pcs"}]),
        _po(id="manual", delivery_deadline_manual=calendar_date_to_wib_iso(day_six), scheduled_delivery_date=day_six, deadline=TODAY),
        _po(id="auto", deadline="2026-11-01"),
        _po(id="beyond", scheduled_delivery_date=day_seven, line_items=[{"name": "Cup", "quantity": 50, "unit": "pcs"}]),
        _po(
            id="reschedule",
            scheduled_delivery_date="2026-10-26",
            delivery_deadline_manual=calendar_date_to_wib_iso("2026-10-28"),
            line_items=[{"name": "Cup", "quantity": 11, "unit": "pcs"}],
            total_quantity=11,
        ),
    ]
    week = aggregate_delivery_plan_qty(orders, lead_time_days=3, today=TODAY, days=7)
    pcs = _series(week, "PCS")
    assert pcs["today_qty"] == 108
    assert pcs["today_po_count"] == 2
    assert pcs["overdue_qty"] == 999
    assert pcs["overdue_po_count"] == 1
    assert pcs["points"][-1]["qty"] == 100
    assert pcs["points"][4]["date"] == "2026-10-28"
    assert pcs["points"][4]["qty"] == 11
    assert pcs["points"][4]["po_count"] == 1
    assert pcs["points"][0]["po_count"] == 2
    assert all(point["date"] >= TODAY for point in pcs["points"])
    assert all("po_count" in point for point in pcs["points"])
    assert sum(point["qty"] for point in pcs["points"]) == 219
    assert pcs["overdue_qty"] == 999
    assert 999 not in {point["qty"] for point in pcs["points"]}
    month = aggregate_delivery_plan_qty(orders, lead_time_days=3, today=TODAY, days=30)
    assert _series(month, "PCS")["points"][7]["date"] == day_seven
    assert _series(month, "PCS")["points"][7]["qty"] == 50


def test_units_stay_split_and_page_size_does_not_apply():
    mixed = _po(
        id="mixed",
        scheduled_delivery_date=TODAY,
        line_items=[
            {"name": "Cup", "quantity": 10, "unit": "pcs"},
            {"name": "Dus", "quantity": 5, "unit": "dus"},
        ],
        qty_delivered=0,
        total_quantity=15,
    )
    blank = _po(
        id="blank",
        scheduled_delivery_date=TODAY,
        line_items=[{"name": "Polos", "quantity": 7, "unit": ""}],
        total_quantity=7,
    )
    legacy = _po(id="legacy", scheduled_delivery_date=TODAY, line_items=[], total_quantity=9, quantity=9)
    casefold = _po(
        id="case",
        scheduled_delivery_date=TODAY,
        line_items=[
            {"name": "A", "quantity": 4, "unit": "pcs"},
            {"name": "B", "quantity": 6, "unit": "PCS"},
        ],
        total_quantity=10,
    )
    service = _po(
        id="service",
        scheduled_delivery_date=TODAY,
        line_items=[
            {"name": "Ongkir", "quantity": 1, "unit": "trip", "item_type": "SERVICE"},
            {"name": "Cup", "quantity": 3, "unit": "pcs", "production_process": "Cetak"},
        ],
        total_quantity=4,
    )
    many = [
        _po(
            id=f"page-{index}",
            po_number=f"PO/PAGE/{index}",
            scheduled_delivery_date=TODAY,
            line_items=[{"name": "Cup", "quantity": 1, "unit": "pcs"}],
            total_quantity=1,
        )
        for index in range(55)
    ]
    body = aggregate_delivery_plan_qty(
        [mixed, blank, legacy, casefold, service, *many],
        lead_time_days=3,
        today=TODAY,
        days=7,
    )
    assert _series(body, "PCS")["today_qty"] == 55 + 10 + 3
    assert "DUS" not in {item["unit"] for item in body["series"]}
    assert _series(body, UNIT_MIXED)["today_qty"] == 15
    assert _series(body, UNIT_NONE)["today_qty"] == 16
    assert "total" not in body
    assert NOTE_SPLIT(body)
    with pytest.raises(ValueError):
        aggregate_delivery_plan_qty([], lead_time_days=3, today=TODAY, days=14)


def NOTE_SPLIT(body):
    text = " ".join(body["notes"])
    return "tidak dijumlahkan" in text and "campuran" in text and "tidak dianggap pcs" in text


def test_layout_permission_matches_plan_and_stays_off_defaults():
    assert finance_widget_allowed("delivery_qty", ["schedule.view"], PRODUCTION_SPEC)
    assert finance_widget_allowed("delivery_qty", ["nav.delivery_schedule"], PRODUCTION_SPEC)
    assert not finance_widget_allowed("delivery_qty", ["dashboard.production.view"], PRODUCTION_SPEC)
    assert "delivery_qty" not in READY_FINANCE()
    placed = default_widgets({"dashboard.production.view", "delivery.schedule.view"}, PRODUCTION_SPEC)
    assert "delivery_qty" not in {item["widget_id"] for item in placed}
    assert "po_aktif" in {item["widget_id"] for item in placed}
    saved = validate_payload(
        [{"widget_id": "delivery_qty", "x": 0, "y": 0, "w": 6, "h": 5, "settings": {}}],
        ["delivery.schedule.view"],
        PRODUCTION_SPEC,
    )
    assert saved[0]["widget_id"] == "delivery_qty"
    with pytest.raises(LayoutError):
        validate_payload(
            [{"widget_id": "delivery_qty", "x": 0, "y": 0, "w": 6, "h": 5, "settings": {}}],
            ["dashboard.production.view"],
            PRODUCTION_SPEC,
        )
    finance_default = default_widgets({"dashboard.finance.view", "delivery.schedule.view"})
    assert "delivery_qty" not in {item["widget_id"] for item in finance_default}


def READY_FINANCE():
    from services.finance_widget_layout import READY_PERMISSION
    return READY_PERMISSION


async def test_http_aggregates_every_order_and_blocks_finance_only(clean_db):
    today = wib_today()
    today_key = today.isoformat()
    later = (today + timedelta(days=10)).isoformat()
    docs = []
    for index in range(55):
        docs.append(_po(
            id=f"http-{index}",
            po_number=f"PO/HTTP/{index}",
            scheduled_delivery_date=today_key,
            line_items=[{"name": "Cup", "quantity": 1, "unit": "pcs"}],
            total_quantity=1,
            qty_delivered=0,
        ))
    docs.append(_po(
        id="http-later",
        scheduled_delivery_date=later,
        line_items=[{"name": "Dus", "quantity": 4, "unit": "dus"}],
        total_quantity=4,
    ))
    docs.append(_po(id="http-cancel", cancelled=True, scheduled_delivery_date=today_key))
    before = docs[0].copy()
    await server.db.orders.insert_many(docs)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: _user(override={"dashboard.finance.view": True})
        finance_only = await client.get("/api/dashboard/widget/qty-rencana-pengiriman")
        app.dependency_overrides[current_user] = lambda: _user(override={"dashboard.sales.view": True})
        sales_only = await client.get("/api/dashboard/widget/qty-rencana-pengiriman")
        app.dependency_overrides[current_user] = lambda: _user(override={"delivery.schedule.view": True})
        allowed = await client.get("/api/dashboard/widget/qty-rencana-pengiriman", params={"days": 7})
        month = await client.get("/api/dashboard/widget/qty-rencana-pengiriman", params={"days": 30})
        bad = await client.get("/api/dashboard/widget/qty-rencana-pengiriman", params={"days": 14})
        for key in ("schedule.view", "nav.delivery_schedule"):
            app.dependency_overrides[current_user] = lambda key=key: _user(override={key: True})
            opened = await client.get("/api/dashboard/widget/qty-rencana-pengiriman")
            assert opened.status_code == 200, key

    assert finance_only.status_code == 403
    assert sales_only.status_code == 403
    assert allowed.status_code == 200
    assert bad.status_code == 400
    body = allowed.json()
    assert _series(body, "PCS")["today_qty"] == 55
    assert body["today"] == today_key
    assert body["timezone"] == "Asia/Jakarta"
    assert body["snapshot"] is True
    assert all(point["date"] <= (today + timedelta(days=6)).isoformat() for point in _series(body, "PCS")["points"])
    assert UNIT_MIXED not in {item["unit"] for item in body["series"]}
    month_body = month.json()
    assert _series(month_body, "DUS")["points"][10]["qty"] == 4
    stored = await server.db.orders.find_one({"id": "http-0"}, {"_id": 0})
    assert stored["qty_delivered"] == before["qty_delivered"]
    assert stored["scheduled_delivery_date"] == before["scheduled_delivery_date"]
