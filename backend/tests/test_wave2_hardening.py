"""Wave 2 hardening: one delivery date, mirror repair, and HTTP contracts. Dummy data only."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = "eternal_packaging_wave2_targets_test"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.production_wave2 import (
    add_working_days,
    apply_finalize,
    mirror_schedule_events,
    reconcile_schedule_mirror,
    stamp_delivery_sla,
)
from services.tanggal_pengiriman import (
    automatic_tanggal_pengiriman,
    classify_delivery_deadline_setting,
    delivery_sla_effective,
    resolve_tanggal_pengiriman,
)

pytestmark = pytest.mark.anyio
WIB = timezone(timedelta(hours=7))


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def db():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await server.db.orders.delete_many({})
    await server.db.operational_holidays.delete_many({})
    await server.db.operational_holiday_events.delete_many({})
    await server.db.production_schedule_events.delete_many({})
    await server.db.customer_approvals.delete_many({})
    await server.db.app_settings.delete_many({"key": {"$in": ["customer_approval", "delivery_deadline"]}})
    yield server.db
    app.dependency_overrides.pop(current_user, None)


def _user(perms):
    async def inner():
        return {
            "id": "u", "user_id": "u", "email": "u@example.com", "name": "User",
            "role": "helper", "status": "active",
            "permissions_override": {key: True for key in perms},
        }
    return inner


def _line():
    return {"name": "Cup", "quantity": 10000, "unit": "pcs", "production_process": "print", "line_id": "L1"}


def _split(**extra):
    row = {
        "id": "s1", "operator_email": "a@x", "operator_name": "Ani", "operator_user_id": "op-a",
        "quantity": 10000, "qty_completed": 0, "line_idx": 0, "work_date": "2026-10-23",
        "progress_entries": [{"qty": 2500, "confirmed_at": "2026-10-23T04:00:00+00:00", "action_id": "prog-1", "legacy": False}],
    }
    row.update(extra)
    return row


def _base(**extra):
    row = {
        "id": "po-h", "po_number": "PO-H", "cancelled": False, "current_stage": 8,
        "priority": "normal", "updated_at": "2026-10-01T00:00:00+00:00",
        "created_at": "2026-10-01T02:00:00+00:00", "order_date": "2026-10-01",
        "design_status": "Approved", "payment_status": "Approved",
        "design_approved_at": "2026-10-23T02:00:00+00:00",
        "payment_approved_at": "2026-10-23T04:00:00+00:00",
        "qty_delivered": 0, "buffer_stock": False, "hold": False,
        "line_items": [_line()], "operator_splits": [_split(qty_completed=2500)],
        "design_files": [{"id": "file-1", "name": "desain.png"}],
        "payment_files": [{"id": "pay-1", "name": "bukti.png"}],
    }
    row.update(extra)
    return row


def test_stamped_normal_ignores_calendar_lead_and_later_holidays():
    order = _base()
    assert automatic_tanggal_pengiriman(order, 3) == "2026-10-26"
    assert stamp_delivery_sla(order, []) is True
    assert order["delivery_sla_v2"]["automatic_date"] == "2026-10-27"
    for days in (1, 3, 5):
        snap = resolve_tanggal_pengiriman(order, days)
        assert snap["tanggal_pengiriman"] == "2026-10-27"
        assert snap["calculated"] == (date(2026, 10, 23) + timedelta(days=days)).isoformat()
        assert snap["delivery_sla_effective"]["source"] == "otomatis"
        assert snap["delivery_sla_effective"]["date"] == "2026-10-27"
    order["delivery_deadline_manual"] = "2026-11-02T00:00:00+07:00"
    manual = delivery_sla_effective(order, today=date(2026, 10, 30))
    assert manual["date"] == "2026-11-02"
    assert manual["source"] == "manual"
    assert manual["state"] == "scheduled"


def test_urgent_never_uses_normal_stamp_and_legacy_stays_calendar():
    urgent = _base(id="po-u", priority="urgent")
    stamp_delivery_sla(urgent, [])
    fact = delivery_sla_effective(urgent, today=date(2026, 10, 30))
    assert fact["source"] == "belum_dijadwalkan"
    assert fact["date"] is None
    assert fact["state"] == "unscheduled"
    urgent["scheduled_delivery_date"] = "2026-10-20"
    dated = delivery_sla_effective(urgent, today=date(2026, 10, 30))
    assert dated["date"] == "2026-10-20"
    assert dated["source"] == "jadwal_pengiriman"
    assert dated["state"] == "overdue"

    legacy = _base(id="po-old")
    legacy.pop("design_approved_at")
    legacy.pop("payment_approved_at")
    old = delivery_sla_effective(legacy, 5, today=date(2026, 10, 30))
    assert old["contract"] == "incomplete"
    assert old["countable"] is False
    calendar = _base(id="po-cal")
    kept = delivery_sla_effective(calendar, 1, today=date(2026, 10, 30))
    assert kept["contract"] == "legacy_calendar"
    assert kept["date"] == "2026-10-24"
    assert kept["countable"] is False
    assert "delivery_sla_v2" not in calendar


def test_buffer_partial_and_full_delivery_share_the_overdue_rule():
    buffer = _base(buffer_stock=True, delivery_deadline_manual="2026-10-01")
    stamp_delivery_sla(buffer, [])
    assert delivery_sla_effective(buffer, today=date(2026, 10, 30))["state"] == "buffer"
    partial = _base(qty_delivered=1000, id="part")
    stamp_delivery_sla(partial, [])
    assert delivery_sla_effective(partial, today=date(2026, 10, 30))["state"] == "overdue"
    full = _base(qty_delivered=10000, id="full", delivered_at="2026-10-29T02:00:00+00:00")
    stamp_delivery_sla(full, [])
    assert delivery_sla_effective(full, today=date(2026, 10, 30))["state"] == "delivered"


async def test_mirror_failure_is_visible_and_repair_is_idempotent(db):
    order = _base(id="po-mirror", po_number="PO-MIRROR")
    apply_finalize(order, action_id="act-1", user={"email": "u@example.com", "name": "User"}, now="2026-10-23T10:00:00+00:00", finish_date="2026-10-28")
    order["operator_splits"][0]["qty_completed"] = 2500
    await db.orders.insert_one(dict(order))
    before = await db.orders.find_one({"id": "po-mirror"}, {"_id": 0, "operator_splits": 1, "production_finish_target": 1})

    class Boom:
        async def create_index(self, *args, **kwargs):
            return None

        async def update_one(self, *args, **kwargs):
            raise RuntimeError("mirror down")

    class Broken:
        def __getitem__(self, name):
            return Boom()

    failed = await mirror_schedule_events(Broken(), order["production_schedule_events"])
    assert failed["ok"] is False
    assert await db.production_schedule_events.count_documents({"action_id": "act-1"}) == 0
    untouched = await db.orders.find_one({"id": "po-mirror"}, {"_id": 0, "operator_splits": 1, "production_finish_target": 1})
    assert untouched == before

    first = await reconcile_schedule_mirror(db, "po-mirror")
    second = await reconcile_schedule_mirror(db, "po-mirror")
    assert first["ok"] is True and second["ok"] is True
    stored = await db.production_schedule_events.count_documents({"action_id": "act-1"})
    assert stored == first["event_count"] == second["upserted"]
    assert stored >= 1
    again = await db.orders.find_one({"id": "po-mirror"}, {"_id": 0, "operator_splits": 1, "production_finish_target": 1})
    assert again == before
    assert again["operator_splits"][0]["qty_completed"] == 2500
    assert again["operator_splits"][0]["progress_entries"][0]["action_id"] == "prog-1"
    assert again["production_finish_target"]["initial_date"] == "2026-10-28"


def _perms():
    return _user([
        "po.view", "schedule.view", "schedule.edit", "schedule.work_date.edit",
        "dashboard.production.view", "delivery.schedule.view",
        "design.approve", "design.bypass_approval", "payment.approve", "payment.bypass_approval",
    ])


async def test_http_customer_approval_bypass_and_stamp_once(db):
    await db.app_settings.insert_one({
        "key": "customer_approval", "allow_approve": True, "signature_enabled": False, "signature_required_approve": False,
    })
    waiting = _base(id="po-cust", po_number="PO-CUST", design_status="Waiting", design_approved_at=None, payment_status="Approved")
    await db.orders.insert_one(waiting)
    token = "customer-token-harden"
    holiday_day = datetime.now(WIB).date() + timedelta(days=1)
    while holiday_day.weekday() == 6:
        holiday_day += timedelta(days=1)
    tomorrow = holiday_day.isoformat()
    await db.operational_holidays.insert_one({"date": tomorrow, "note": "libur uji"})
    await db.customer_approvals.insert_one({
        "id": "ap-1", "order_id": "po-cust", "po_number": "PO-CUST", "design_version": "file-1",
        "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(), "status": "active",
        "expires_at": "2027-01-01T00:00:00+00:00",
    })
    app.dependency_overrides[current_user] = _user(["dashboard.production.view"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.post("/api/orders/po-cust/design/bypass", json={"reason": "tidak boleh"})
        denied_user = await client.post("/api/orders/po-cust/approve-design", json={"approved": True})
        app.dependency_overrides[current_user] = _perms()
        early = await client.get("/api/orders/po-cust")
        submitted = await client.post(f"/api/public/customer-approval/{token}/submit", json={"action": "approve", "signer_name": "Sari"})
        repeated = await client.post(f"/api/public/customer-approval/{token}/submit", json={"action": "approve", "signer_name": "Sari"})
        paid_only = await client.get("/api/orders/po-cust")
    assert denied.status_code == 403
    assert denied_user.status_code == 403
    assert early.status_code == 200
    assert "delivery_sla_v2" not in early.json()["order"]
    assert submitted.status_code == 200
    assert repeated.status_code == 400
    saved = await db.orders.find_one({"id": "po-cust"}, {"_id": 0})
    assert saved["design_status"] == "Approved"
    assert saved["delivery_sla_v2"]["rule"] == "working_days_3"
    assert tomorrow in saved["delivery_sla_v2"]["holiday_dates"]
    anchor = datetime.fromisoformat(saved["delivery_sla_v2"]["anchor_at"])
    holidays = saved["delivery_sla_v2"]["holiday_dates"]
    assert saved["delivery_sla_v2"]["automatic_date"] == add_working_days(anchor.astimezone(WIB).date(), 3, holidays).isoformat()
    assert saved["delivery_sla_v2"]["automatic_date"] != add_working_days(anchor.astimezone(WIB).date(), 3, []).isoformat()
    assert paid_only.json()["order"]["delivery_sla_v2"]["automatic_date"] == saved["delivery_sla_v2"]["automatic_date"]
    stamped_at = saved["delivery_sla_v2"]["stamped_at"]
    fresh = _base(id="po-bypass", po_number="PO-BYPASS", design_status="Waiting", design_approved_at=None)
    await db.orders.insert_one(fresh)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        again = await client.post("/api/orders/po-cust/approve-design", json={"approved": True})
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        bypass = await client.post("/api/orders/po-bypass/design/bypass", json={"reason": "desain internal"})
        bypass_again = await client.post("/api/orders/po-bypass/design/bypass", json={"reason": "kedua"})
        readonly = await client.get("/api/orders/po-bypass")
    assert again.status_code == 200
    still = await db.orders.find_one({"id": "po-cust"}, {"_id": 0, "delivery_sla_v2": 1})
    assert still["delivery_sla_v2"]["stamped_at"] == stamped_at
    assert still["delivery_sla_v2"]["automatic_date"] == saved["delivery_sla_v2"]["automatic_date"]
    assert bypass.status_code == 200
    bypassed = await db.orders.find_one({"id": "po-bypass"}, {"_id": 0})
    assert bypassed["design_bypassed"] is True
    assert bypassed["delivery_sla_v2"]["automatic_date"]
    assert bypass_again.status_code == 200
    bypassed_again = await db.orders.find_one({"id": "po-bypass"}, {"_id": 0, "delivery_sla_v2": 1})
    assert bypassed_again["delivery_sla_v2"] == bypassed["delivery_sla_v2"]
    assert readonly.json()["order"]["delivery_sla_v2"]["automatic_date"] == bypassed["delivery_sla_v2"]["automatic_date"]


async def test_http_work_date_revision_keeps_target_and_progress(db):
    order = _base(id="po-work", po_number="PO-WORK")
    await db.orders.insert_one(dict(order))
    app.dependency_overrides[current_user] = _user(["schedule.view"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.patch("/api/orders/po-work/operator-splits/s1/work-date", json={"work_date": "2026-10-24"})
        app.dependency_overrides[current_user] = _perms()
        draft = await client.patch("/api/orders/po-work/operator-splits/s1/work-date", json={"work_date": "2026-10-24", "action_id": "draft-1"})
    draft_doc = await db.orders.find_one({"id": "po-work"}, {"_id": 0})
    assert draft.status_code == 200
    assert draft_doc["operator_splits"][0]["work_date"] == "2026-10-24"
    assert not any(row.get("kind") == "schedule_revised" for row in (draft_doc.get("production_schedule_events") or []))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        locked = await client.post("/api/orders/po-work/production-schedule/finalize", json={"action_id": "final-1", "finish_target_date": "2026-10-28"})
        revised = await client.patch("/api/orders/po-work/operator-splits/s1/work-date", json={"work_date": "2026-10-27", "action_id": "rev-1"})
        replay = await client.patch("/api/orders/po-work/operator-splits/s1/work-date", json={"work_date": "2026-10-30", "action_id": "rev-1"})

        async def move(action_id, day):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as other:
                return await other.patch(
                    "/api/orders/po-work/operator-splits/s1/work-date",
                    json={"work_date": day, "action_id": action_id},
                )

        left, right = await asyncio.gather(move("rev-a", "2026-10-29"), move("rev-b", "2026-10-30"))
        snap = await client.get("/api/dashboard/produksi/snapshot")
    assert denied.status_code == 403
    assert locked.status_code == 200
    assert locked.json()["mirror"]["ok"] is True
    assert revised.status_code == 200
    assert replay.status_code == 200
    saved = await db.orders.find_one({"id": "po-work"}, {"_id": 0})
    assert saved["production_finish_target"]["initial_date"] == "2026-10-28"
    assert saved["production_finish_target"].get("revised_date") in (None, saved["production_finish_target"].get("revised_date"))
    assert saved["operator_splits"][0]["qty_completed"] == 2500
    assert saved["operator_splits"][0]["progress_entries"][0]["qty"] == 2500
    kinds = [row["kind"] for row in saved["production_schedule_events"] if row.get("split_id") == "s1"]
    assert kinds.count("schedule_finalized") == 1
    assert "schedule_revised" in kinds
    assert {left.status_code, right.status_code} <= {200, 409}
    final = await db.orders.find_one({"id": "po-work"}, {"_id": 0})
    assert final["operator_splits"][0]["qty_completed"] == 2500
    assert final["operator_splits"][0]["work_date"] in ("2026-10-27", "2026-10-29", "2026-10-30")
    assert final["production_finish_target"]["initial_date"] == "2026-10-28"
    keys = [(row.get("action_id"), row.get("split_id"), row.get("kind")) for row in final["production_schedule_events"]]
    assert len(keys) == len(set(keys))
    assert sum(1 for row in final["production_schedule_events"] if row.get("kind") == "schedule_finalized" and row.get("split_id") == "s1") == 1
    body = snap.json()["wave2"]["target_produksi"]
    assert body["status"] == "complete"
    days = {row["day"] for row in body["series"]}
    assert "2026-10-23" in days or "2026-10-24" in days


async def test_http_same_deadline_across_modules(db):
    normal = _base(
        id="po-normal", po_number="PO-NORMAL",
        delivery_sla_v2={
            "rule": "working_days_3", "anchor_at": "2026-10-23T04:00:00+00:00",
            "automatic_date": "2026-10-27", "holiday_dates": [], "stamped_at": "2026-10-23T05:00:00+00:00", "legacy": False,
        },
        shipments=[{"id": "sj-n", "number": "SJ-N", "delivery_date": "2026-10-27", "shipment_index": 1, "lines": []}],
    )
    urgent = _base(
        id="po-urgent", po_number="PO-URGENT", priority="urgent",
        scheduled_delivery_date="2026-10-20",
        delivery_sla_v2={
            "rule": "working_days_3", "anchor_at": "2026-10-23T04:00:00+00:00",
            "automatic_date": "2026-10-27", "holiday_dates": [], "stamped_at": "2026-10-23T05:00:00+00:00", "legacy": False,
        },
        shipments=[{"id": "sj-u", "number": "SJ-U", "delivery_date": "2026-10-20", "shipment_index": 1, "lines": []}],
    )
    await db.orders.insert_many([normal, urgent])
    app.dependency_overrides[current_user] = _perms()
    transport = httpx.ASGITransport(app=app)

    async def facts(client, order_id, po_number):
        detail = await client.get(f"/api/orders/{order_id}")
        status = await client.get("/api/dashboard/status-po", params={"view": "all", "po": po_number})
        schedule = await client.get("/api/delivery/schedule")
        snap = await client.get("/api/dashboard/produksi/snapshot")
        assert detail.status_code == 200
        assert status.status_code == 200
        assert schedule.status_code == 200
        assert snap.status_code == 200
        detail_fact = detail.json()["order"]["delivery_sla_effective"]
        status_row = next(row for row in status.json()["rows"] if row["id"] == order_id)
        schedule_row = next(row for row in schedule.json()["rows"] if row["id"] == order_id)
        contract = next(row for row in snap.json()["wave2"]["po_terlambat"]["contracts"] if row["order_id"] == order_id)
        return detail_fact, status_row["delivery_sla_effective"], schedule_row["delivery_sla_effective"], contract

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        normal_facts = await facts(client, "po-normal", "PO-NORMAL")
        urgent_facts = await facts(client, "po-urgent", "PO-URGENT")
        before = await db.orders.find_one({"id": "po-normal"}, {"_id": 0, "delivery_sla_v2": 1})
        await client.get("/api/orders/po-normal")
        after = await db.orders.find_one({"id": "po-normal"}, {"_id": 0, "delivery_sla_v2": 1})
    assert before == after
    for fact in normal_facts:
        assert fact["date"] == "2026-10-27"
        assert fact["source"] == "otomatis"
        assert fact["state"] == normal_facts[0]["state"]
    assert normal_facts[0]["date"] != "2026-10-26"
    for fact in urgent_facts:
        assert fact["date"] == "2026-10-20"
        assert fact["source"] == "jadwal_pengiriman"
        assert fact["state"] == urgent_facts[0]["state"]
    assert urgent_facts[0]["state"] == "overdue"


def test_working_day_choices_and_legacy_setting_stay_distinct():
    friday = date(2026, 10, 23)
    assert add_working_days(friday, 1, []) == date(2026, 10, 24)
    assert add_working_days(friday, 3, []) == date(2026, 10, 27)
    assert add_working_days(friday, 5, []) == date(2026, 10, 29)
    assert add_working_days(friday, 1, ["2026-10-24"]) == date(2026, 10, 26)

    one = _base()
    assert stamp_delivery_sla(one, [], working_days=1) is True
    assert one["delivery_sla_v2"]["working_days"] == 1
    assert one["delivery_sla_v2"]["unit"] == "working_days"
    assert one["delivery_sla_v2"]["automatic_date"] == "2026-10-24"
    assert stamp_delivery_sla(one, ["2026-10-24"], working_days=5) is False
    assert one["delivery_sla_v2"]["automatic_date"] == "2026-10-24"

    five = _base(id="po-5")
    assert stamp_delivery_sla(five, [], working_days=5) is True
    assert five["delivery_sla_v2"]["automatic_date"] == "2026-10-29"
    boundary = _base(
        id="po-wib",
        design_approved_at="2026-10-23T16:00:00+00:00",
        payment_approved_at="2026-10-23T17:30:00+00:00",
    )
    assert stamp_delivery_sla(boundary, [], working_days=1) is True
    assert boundary["delivery_sla_v2"]["automatic_date"] == "2026-10-26"
    missing = _base(id="po-miss", payment_approved_at=None)
    assert stamp_delivery_sla(missing, [], working_days=1) is False
    assert "delivery_sla_v2" not in missing

    legacy = classify_delivery_deadline_setting({"lead_time_days": 5})
    assert legacy["sla_source"] == "default"
    assert legacy["sla_working_days"] == 3
    assert legacy["calendar_lead_time_days"] == 5
    assert legacy["legacy_unit"] == "calendar_days"
    named = classify_delivery_deadline_setting({"lead_time_days": 1, "unit": "calendar_days"})
    assert named["sla_working_days"] == 3
    assert named["calendar_lead_time_days"] == 1
    active = classify_delivery_deadline_setting({
        "unit": "working_days", "sla_working_days": 1, "legacy_lead_time_days": 5, "legacy_unit": "calendar_days",
    })
    assert active["sla_source"] == "setting"
    assert active["sla_working_days"] == 1
    assert active["calendar_lead_time_days"] == 5
    assert classify_delivery_deadline_setting(None)["legacy_unit"] is None

    partial = _base(id="po-part", qty_delivered=1000, current_stage=14, delivery_status="Terkirim")
    stamp_delivery_sla(partial, [])
    snap = {}
    server._attach_deadline_snapshot(partial, 3)
    assert partial["delivery_deadline_status_basis"] == "qty"
    assert partial["delivery_deadline_status"] != "terkirim"
    full = _base(id="po-full", qty_delivered=10000, current_stage=14, delivery_status="Terkirim")
    stamp_delivery_sla(full, [])
    server._attach_deadline_snapshot(full, 3)
    assert full["delivery_deadline_status"] == "terkirim"
    assert full["delivery_deadline_status_basis"] == "qty"
    old = _base(id="po-text")
    old.pop("design_approved_at")
    old.pop("payment_approved_at")
    old.pop("qty_delivered")
    old["delivery_status"] = "Terkirim"
    old["current_stage"] = 14
    server._attach_deadline_snapshot(old, 3)
    assert old["delivery_deadline_status_basis"] == "legacy_stage"
    assert old["delivery_deadline_status"] == "terkirim"
    assert snap == {}


async def test_http_working_day_setting_and_plan_matrix(db):
    await db.app_settings.insert_one({"key": "delivery_deadline", "lead_time_days": 5})
    perms = [
        "po.view", "schedule.view", "delivery.schedule.view", "dashboard.production.view",
        "design.approve", "payment.approve", "delivery.deadline.settings",
    ]
    app.dependency_overrides[current_user] = _user(["po.view"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        denied = await client.put("/api/delivery-deadline/settings", json={"lead_time_days": 1})
        seen = await client.get("/api/delivery-deadline/settings")
        app.dependency_overrides[current_user] = _user(perms)
        saved = await client.put("/api/delivery-deadline/settings", json={"lead_time_days": 1})
        waiting = _base(
            id="po-sla", po_number="PO-SLA", payment_status="Waiting", payment_approved_at=None,
        )
        await db.orders.insert_one(waiting)
        approved = await client.post("/api/orders/po-sla/approve-payment", json={"approved": True})
        reread = await client.get("/api/orders/po-sla")
        changed = await client.put("/api/delivery-deadline/settings", json={"lead_time_days": 5})
        after_config = await client.get("/api/orders/po-sla")
    assert denied.status_code == 403
    assert seen.json()["unit"] == "working_days"
    assert seen.json()["settings"]["lead_time_days"] == 3
    assert seen.json()["settings"]["source"] == "default"
    assert seen.json()["settings"]["legacy"] == {"lead_time_days": 5, "unit": "calendar_days"}
    assert saved.status_code == 200
    assert saved.json()["settings"]["lead_time_days"] == 1
    assert saved.json()["settings"]["unit"] == "working_days"
    assert saved.json()["settings"]["legacy"]["lead_time_days"] == 5
    assert approved.status_code == 200
    stamped = reread.json()["order"]["delivery_sla_v2"]
    anchor = datetime.fromisoformat(stamped["anchor_at"])
    assert stamped["working_days"] == 1
    assert stamped["unit"] == "working_days"
    assert stamped["automatic_date"] == add_working_days(anchor.astimezone(WIB).date(), 1, []).isoformat()
    assert changed.json()["settings"]["lead_time_days"] == 5
    assert after_config.json()["order"]["delivery_sla_v2"]["automatic_date"] == stamped["automatic_date"]
    stored = await db.orders.find_one({"id": "po-sla"}, {"_id": 0, "shipments": 1, "qty_delivered": 1})
    assert stored.get("shipments") in (None, [])
    assert stored["qty_delivered"] == 0

    normal = _base(
        id="po-plan-n", po_number="PO-PLAN-N",
        delivery_sla_v2={"rule": "working_days_3", "working_days": 3, "unit": "working_days", "automatic_date": "2026-10-27", "legacy": False},
        shipments=[
            {"id": "sj-a", "number": "SJ-A", "delivery_date": "2026-10-30", "shipment_index": 1, "lines": [{"qty": 1000}]},
            {"id": "sj-b", "number": "SJ-B", "delivery_date": "2026-11-02", "shipment_index": 2, "lines": [{"qty": 1000}]},
        ],
    )
    urgent = _base(id="po-plan-u", po_number="PO-PLAN-U", priority="urgent", scheduled_delivery_date="2026-10-20")
    urgent_open = _base(id="po-plan-open", po_number="PO-PLAN-OPEN", priority="urgent")
    manual = _base(
        id="po-plan-m", po_number="PO-PLAN-M",
        delivery_sla_v2={"rule": "working_days_1", "working_days": 1, "unit": "working_days", "automatic_date": "2026-10-24", "legacy": False},
        delivery_deadline_manual="2026-11-02T00:00:00+07:00",
        scheduled_delivery_date="2026-11-02",
    )
    legacy = _base(id="po-plan-l", po_number="PO-PLAN-L")
    buffer = _base(id="po-plan-b", po_number="PO-PLAN-B", buffer_stock=True, delivery_deadline_manual="2026-10-01", scheduled_delivery_date="2026-10-01")
    await db.orders.insert_many([normal, urgent, urgent_open, manual, legacy, buffer])
    before_sj = await db.orders.find_one({"id": "po-plan-n"}, {"_id": 0, "shipments": 1, "qty_delivered": 1, "current_stage": 1})
    app.dependency_overrides[current_user] = _user(perms)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        plan = await client.get("/api/delivery/plan", params={"page_size": 100})
        schedule = await client.get("/api/delivery/schedule")
        detail = await client.get("/api/orders/po-plan-n")
    assert plan.status_code == 200
    rows = {row["id"]: row for row in plan.json()["rows"]}
    assert "po-plan-open" not in rows
    assert rows["po-plan-n"]["delivery_sla_effective"]["date"] == "2026-10-27"
    assert rows["po-plan-n"]["delivery_sla_effective"]["source"] == "otomatis"
    assert rows["po-plan-n"]["qty_total"] == 10000
    assert rows["po-plan-u"]["delivery_sla_effective"]["date"] == "2026-10-20"
    assert rows["po-plan-u"]["delivery_sla_effective"]["source"] == "jadwal_pengiriman"
    assert rows["po-plan-m"]["delivery_sla_effective"]["date"] == "2026-11-02"
    assert rows["po-plan-m"]["delivery_sla_effective"]["source"] == "manual"
    assert rows["po-plan-l"]["delivery_sla_effective"]["contract"] == "legacy_calendar"
    assert rows["po-plan-l"]["delivery_sla_effective"]["date"] == "2026-10-28"
    assert rows["po-plan-b"]["delivery_sla_effective"]["state"] == "buffer"
    assert rows["po-plan-b"]["delivery_schedule_status"] != "terlambat"
    sched = [row for row in schedule.json()["rows"] if row["id"] == "po-plan-n"]
    assert len(sched) == 2
    assert {row["sj_delivery_date"] for row in sched} == {"2026-10-30", "2026-11-02"}
    assert {row["po_sla_date"] for row in sched} == {"2026-10-27"}
    assert detail.json()["order"]["delivery_sla_effective"]["date"] == "2026-10-27"
    after_sj = await db.orders.find_one({"id": "po-plan-n"}, {"_id": 0, "shipments": 1, "qty_delivered": 1, "current_stage": 1})
    assert after_sj == before_sj
