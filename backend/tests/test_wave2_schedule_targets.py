"""Wave 2 schedule targets, holiday calendar, and delivery SLA. Dummy orders only."""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ["DB_NAME"] = "eternal_packaging_wave2_targets_test"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import OPT_IN_ONLY_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, director_full_permission_set
from server import app, current_user
from services.finance_widget_layout import DEFAULT_ORDER as FINANCE_DEFAULT_ORDER
from services.operator_schedule import apply_operator_assignments, cancel_split_remaining
from services.production_analytics import build_operator_analytics
from services.production_dashboard_snapshot import build_production_snapshot
from services.production_wave2 import (
    ScheduleError,
    add_working_days,
    apply_finalize,
    apply_finish_target,
    build_wave2,
    guard_finalized_move,
    is_working_day,
    mutate_holiday,
    operator_targets,
    record_assignment_change,
    stamp_delivery_sla,
)
from services.production_widget_layout import PRODUCTION_SPEC
from services.tanggal_pengiriman import automatic_tanggal_pengiriman

pytestmark = pytest.mark.anyio
HEAD = {"id": "kepala", "email": "kepala@example.com", "name": "Kepala", "role": "kepala_produksi"}


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
    yield server.db
    app.dependency_overrides.pop(current_user, None)


def _split(**extra):
    row = {
        "id": "s1",
        "operator_email": "a@x",
        "operator_name": "Ani",
        "operator_user_id": "op-a",
        "quantity": 10000,
        "qty_completed": 0,
        "line_idx": 0,
        "work_date": "2026-10-23",
        "original_quantity": 99999,
    }
    row.update(extra)
    return row


def _order(**extra):
    row = {
        "id": "po-1",
        "po_number": "PO-1",
        "cancelled": False,
        "current_stage": 8,
        "priority": "normal",
        "updated_at": "2026-10-01T00:00:00+00:00",
        "created_at": "2026-10-01T02:00:00+00:00",
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": "2026-10-23T02:00:00+00:00",
        "payment_approved_at": "2026-10-23T04:00:00+00:00",
        "qty_delivered": 0,
        "buffer_stock": False,
        "hold": False,
        "line_items": [{
            "name": "Cup", "quantity": 10000, "unit": "pcs",
            "production_process": "print", "line_id": "L1",
        }],
        "operator_splits": [_split()],
    }
    row.update(extra)
    return row


def _user(perms):
    async def inner():
        return {
            "id": "u", "user_id": "u", "email": "u@example.com", "name": "User",
            "role": "helper", "status": "active",
            "permissions_override": {key: True for key in perms},
        }
    return inner


def test_finalize_locks_initial_target_and_replays():
    order = _order()
    assert apply_finalize(order, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00", finish_date="2026-10-28") == "ok"
    assert order["production_schedule"]["state"] == "finalized"
    assert order["production_finish_target"]["initial_date"] == "2026-10-28"
    assert order["production_finish_target"]["revised_date"] is None
    finals = [row for row in order["production_schedule_events"] if row["kind"] == "schedule_finalized" and row["split_id"] == "s1"]
    assert len(finals) == 1
    assert finals[0]["qty"] == 10000
    assert finals[0]["qty"] != 99999
    assert apply_finalize(order, action_id="act-1", user=HEAD, now="2026-10-23T11:00:00+00:00") == "replay"
    assert len(order["production_schedule_events"]) == len(finals) + 1
    with pytest.raises(ScheduleError) as exc:
        apply_finalize(order, action_id="act-2", user=HEAD, now="2026-10-23T12:00:00+00:00")
    assert exc.value.code == "SCHEDULE_ALREADY_FINAL"


def test_revision_qty_move_cancel_and_locked_awal():
    order = _order()
    apply_finalize(order, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00", finish_date="2026-10-28")
    awal_qty = next(row["qty"] for row in order["production_schedule_events"] if row["split_id"] == "s1")
    before = copy.deepcopy(order["operator_splits"])
    order["operator_splits"][0]["quantity"] = 15000
    assert record_assignment_change(order, before, action_id="up", user=HEAD, now="2026-10-24T01:00:00+00:00") == "ok"
    assert next(row["qty"] for row in order["production_schedule_events"] if row["kind"] == "schedule_finalized" and row["split_id"] == "s1") == awal_qty
    assert order["production_finish_target"]["initial_date"] == "2026-10-28"

    order["operator_splits"][0]["qty_completed"] = 7000
    before = copy.deepcopy(order["operator_splits"])
    with pytest.raises(ValueError):
        apply_operator_assignments(order, [{
            "id": "s1", "operator_email": "a@x", "operator_name": "Ani", "quantity": 6000,
            "line_idx": 0, "work_date": "2026-10-23",
        }], HEAD, "2026-10-24T02:00:00+00:00", new_id_fn=lambda: "new")
    assert order["operator_splits"][0]["qty_completed"] == 7000
    apply_operator_assignments(order, [{
        "id": "s1", "operator_email": "a@x", "operator_name": "Ani", "quantity": 7000,
        "line_idx": 0, "work_date": "2026-10-23",
    }], HEAD, "2026-10-24T02:00:00+00:00", new_id_fn=lambda: "new")
    assert order["operator_splits"][0]["qty_completed"] == 7000
    assert order["operator_splits"][0]["work_date"] == "2026-10-23"
    record_assignment_change(order, before, action_id="down", user=HEAD, now="2026-10-24T02:00:00+00:00")

    moved = _order()
    apply_finalize(moved, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00")
    moved["operator_splits"][0]["qty_completed"] = 4000
    with pytest.raises(ScheduleError) as blocked:
        guard_finalized_move(moved["operator_splits"], [{
            "id": "s1", "operator_email": "b@x", "quantity": 10000,
        }])
    assert blocked.value.code == "SCHEDULE_MOVE_NEW_ROW"
    before = copy.deepcopy(moved["operator_splits"])
    apply_operator_assignments(moved, [
        {"id": "s1", "operator_email": "a@x", "operator_name": "Ani", "quantity": 4000, "line_idx": 0, "work_date": "2026-10-23"},
        {"operator_email": "b@x", "operator_name": "Budi", "operator_user_id": "op-b", "quantity": 6000, "line_idx": 0, "work_date": "2026-10-24"},
    ], HEAD, "2026-10-24T03:00:00+00:00", new_id_fn=lambda: "s2")
    origin = next(row for row in moved["operator_splits"] if row["id"] == "s1")
    assert origin["qty_completed"] == 4000
    assert origin["operator_email"] == "a@x"
    record_assignment_change(moved, before, action_id="move", user=HEAD, now="2026-10-24T03:00:00+00:00")
    assert any(row.get("from_operator_email") == "a@x" or row.get("split_id") == "s2" for row in moved["production_schedule_events"])

    cancelled = _order()
    apply_finalize(cancelled, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00")
    cancelled["operator_splits"][0]["qty_completed"] = 2500
    before = copy.deepcopy(cancelled["operator_splits"])
    cancel_split_remaining(cancelled, "s1", 7500, HEAD, "2026-10-24T04:00:00+00:00", "sisa")
    kept = cancelled["operator_splits"][0]
    assert kept["qty_completed"] == 2500
    assert kept["quantity"] == 2500
    assert kept["work_date"] == "2026-10-23"
    record_assignment_change(cancelled, before, action_id="cancel", user=HEAD, now="2026-10-24T04:00:00+00:00")


def test_cohort_is_not_calendar_output_and_can_pass_100():
    order = _order()
    apply_finalize(order, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00")
    order["operator_splits"][0]["qty_completed"] = 7000
    order["operator_splits"][0]["progress_entries"] = [{
        "qty": 7000, "confirmed_at": "2026-10-24T02:00:00+00:00", "operator_user_id": "op-a",
    }]
    body = build_wave2([order], today=date(2026, 10, 24))
    series = body["target_produksi"]["series"]
    assert series[0]["day"] == "2026-10-23"
    assert series[0]["awal"] == 10000
    assert series[0]["realisasi"] == 7000
    assert all(row["day"] != "2026-10-24" for row in series)

    revised = _order()
    apply_finalize(revised, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00")
    before = copy.deepcopy(revised["operator_splits"])
    revised["operator_splits"][0]["quantity"] = 15000
    revised["operator_splits"][0]["qty_completed"] = 12000
    revised["operator_splits"][0]["progress_entries"] = [{
        "qty": 12000, "confirmed_at": "2026-10-24T02:00:00+00:00", "action_id": "done-1", "legacy": False,
    }]
    record_assignment_change(revised, before, action_id="rev", user=HEAD, now="2026-10-24T01:00:00+00:00")
    targets = operator_targets([revised])
    assert targets["awal"]["op-a"]["pcs"] == 10000
    assert targets["revisi"]["op-a"]["pcs"] == 15000
    assert targets["realisasi"]["op-a"]["pcs"] == 12000
    analytics = build_operator_analytics([revised], today=date(2026, 10, 24))
    row = analytics["performance"]["ranking"]["pcs"][0]
    assert row["pencapaian_awal"] == 120.0
    assert row["pencapaian_revisi"] == 80.0

    draft = _order()
    assert operator_targets([draft])["available"] is False
    assert build_wave2([draft], today=date(2026, 10, 24))["target_produksi"]["status"] == "unlocked"


def test_calendar_and_delivery_rules():
    assert is_working_day(date(2026, 10, 25), []) is False
    assert is_working_day(date(2026, 10, 24), []) is True
    assert is_working_day(date(2026, 10, 24), ["2026-10-24"]) is False
    friday = date(2026, 10, 23)
    assert add_working_days(friday, 3, []).isoformat() == "2026-10-27"
    assert add_working_days(friday, 3, ["2026-10-24"]).isoformat() == "2026-10-28"
    legacy = _order()
    assert automatic_tanggal_pengiriman(legacy, 3) == "2026-10-26"
    assert stamp_delivery_sla(legacy, []) is True
    assert legacy["delivery_sla_v2"]["automatic_date"] == "2026-10-27"
    stamped = legacy["delivery_sla_v2"]["automatic_date"]
    assert stamp_delivery_sla(legacy, ["2026-10-24"]) is False
    assert legacy["delivery_sla_v2"]["automatic_date"] == stamped

    urgent = _order(priority="urgent")
    late = build_wave2([urgent], today=date(2026, 10, 30))
    assert late["po_terlambat"]["unscheduled"] == 1
    urgent["scheduled_delivery_date"] = "2026-10-20"
    assert build_wave2([urgent], today=date(2026, 10, 30))["po_terlambat"]["overdue"] == 1

    normal = _order()
    stamp_delivery_sla(normal, [])
    normal["delivery_deadline_manual"] = "2026-11-02T00:00:00+07:00"
    manual = build_wave2([normal], today=date(2026, 10, 30))
    assert manual["po_terlambat"]["rows"] == [] or manual["po_terlambat"]["overdue"] == 0

    buffer = _order(buffer_stock=True, delivery_deadline_manual="2026-10-01")
    assert build_wave2([buffer], today=date(2026, 10, 30))["po_terlambat"]["overdue"] == 0

    later = _order(design_approved_at="2026-10-22T02:00:00+00:00", payment_approved_at="2026-10-23T04:00:00+00:00")
    stamp_delivery_sla(later, [])
    assert later["delivery_sla_v2"]["automatic_date"] == "2026-10-27"
    incomplete = _order(payment_approved_at=None)
    assert stamp_delivery_sla(incomplete, []) is False
    assert "delivery_sla_v2" not in incomplete

    old = _order()
    old.pop("design_approved_at")
    old.pop("payment_approved_at")
    body = build_wave2([old], today=date(2026, 10, 30))
    assert body["po_terlambat"]["legacy"] == 1

    partial = _order(qty_delivered=1000, delivery_deadline_manual="2026-10-01", surat_jalan_files=[{"id": "a"}, {"id": "b"}])
    assert build_wave2([partial], today=date(2026, 10, 30))["po_terlambat"]["overdue"] == 1
    full = _order(qty_delivered=10000, delivered_at="2026-10-29T02:00:00+00:00", delivery_deadline_manual="2026-10-01")
    assert build_wave2([full], today=date(2026, 10, 30))["po_terlambat"]["overdue"] == 0


def test_aging_accuracy_and_bottleneck():
    today = date(2026, 10, 30)
    open_order = _order()
    apply_finalize(open_order, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00", finish_date="2026-10-24")
    body = build_wave2([open_order], today=today)
    assert body["aging"]["produksi"]["aktif"] == 1
    assert body["aging"]["total"]["aktif"] == 1
    assert body["bottleneck"]["po"] == 1
    assert body["bottleneck"]["lines"] == 1
    assert body["ketepatan"]["produksi"]["berjalan"] == 1

    done = _order()
    apply_finalize(done, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00", finish_date="2026-10-29")
    done["operator_splits"][0]["qty_completed"] = 10000
    done["packing_info"] = {"lines": [{"line_idx": 0, "line_id": "L1", "packed_qty": 10000}], "events": [{"created_at": "2026-10-28T03:00:00+00:00"}]}
    done["qty_delivered"] = 10000
    done["delivered_at"] = "2026-10-29T03:00:00+00:00"
    done["delivery_deadline_manual"] = "2026-10-29"
    finished = build_wave2([done], today=today)
    assert finished["aging"]["produksi"]["selesai"] == 1
    assert finished["aging"]["total"]["selesai"] == 1
    assert finished["ketepatan"]["produksi"]["tepat"] == 1
    assert finished["ketepatan"]["pengiriman"]["tepat"] == 1
    assert finished["bottleneck"]["po"] == 0
    assert finished["bottleneck"]["pengiriman_belum_selesai"] == 0

    missing_pack = copy.deepcopy(done)
    missing_pack["packing_info"]["events"] = []
    missing_pack["id"] = "po-miss"
    assert build_wave2([missing_pack], today=today)["aging"]["produksi"]["histori"] == 1

    hold = _order(hold=True, id="hold")
    apply_finalize(hold, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00", finish_date="2026-10-01")
    pending = _order(id="wait", design_status="Waiting", design_approved_at=None)
    ship = copy.deepcopy(done)
    ship["id"] = "ship"
    ship["qty_delivered"] = 1000
    ship.pop("delivered_at", None)
    mixed = build_wave2([hold, pending, ship], today=today)
    assert mixed["bottleneck"]["po"] == 0
    assert mixed["bottleneck"]["pengiriman_belum_selesai"] == 1

    revised_target = _order()
    apply_finalize(revised_target, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00", finish_date="2026-10-28")
    assert apply_finish_target(revised_target, action_id="fin-2", finish_date="2026-10-30", user=HEAD, now="2026-10-25T01:00:00+00:00") == "ok"
    assert revised_target["production_finish_target"]["initial_date"] == "2026-10-28"
    assert revised_target["production_finish_target"]["revised_date"] == "2026-10-30"
    assert apply_finish_target(revised_target, action_id="fin-2", finish_date="2026-11-01", user=HEAD, now="2026-10-26T01:00:00+00:00") == "replay"
    assert revised_target["production_finish_target"]["revised_date"] == "2026-10-30"

    foreign = _order(line_items=[{
        "name": "Roll", "quantity": 4, "unit": "pack", "production_process": "print", "line_id": "P1",
    }])
    foreign["operator_splits"][0]["quantity"] = 4
    apply_finalize(foreign, action_id="act-1", user=HEAD, now="2026-10-23T10:00:00+00:00")
    series = build_wave2([foreign], today=today)["target_realisasi"]["series"]
    assert series[0]["unit"] == "pack"


def test_permission_catalog_and_layout_contracts():
    assert "settings.operational_calendar.manage" in OPT_IN_ONLY_PERMISSIONS
    assert "settings.operational_calendar.manage" not in director_full_permission_set()
    assert "settings.operational_calendar.manage" not in str(ROLE_DEFAULT_PERMISSIONS)
    assert "target_produksi" in PRODUCTION_SPEC["ready"]
    assert "target_produksi" not in PRODUCTION_SPEC["order"]
    assert "kesiapan" not in PRODUCTION_SPEC["order"]
    assert FINANCE_DEFAULT_ORDER[0] == "cash"
    assert "bottleneck" not in FINANCE_DEFAULT_ORDER


async def test_holiday_permission_and_read_only_snapshot(db):
    added = await mutate_holiday(db, action="add", action_id="h1", day="2026-10-24", note="Libur", user=HEAD, now="2026-10-01T00:00:00+00:00")
    assert added["replay"] is False
    replay = await mutate_holiday(db, action="add", action_id="h1", day="2026-10-24", note="Libur", user=HEAD, now="2026-10-01T00:00:00+00:00")
    assert replay["replay"] is True
    await mutate_holiday(db, action="update", action_id="h2", day="2026-10-24", note="Cuti", user=HEAD, now="2026-10-02T00:00:00+00:00")
    stored = await db.operational_holidays.find_one({"date": "2026-10-24"}, {"_id": 0})
    assert stored["note"] == "Cuti"
    await mutate_holiday(db, action="delete", action_id="h3", day="2026-10-24", note="", user=HEAD, now="2026-10-03T00:00:00+00:00")
    assert await db.operational_holidays.count_documents({"date": "2026-10-24"}) == 0
    with pytest.raises(ScheduleError):
        await mutate_holiday(db, action="add", action_id="bad", day="bukan-tanggal", note="", user=HEAD, now="2026-10-01T00:00:00+00:00")

    order = _order()
    await db.orders.insert_one(dict(order))
    before = await db.orders.find_one({"id": "po-1"}, {"_id": 0})
    app.dependency_overrides[current_user] = _user(["dashboard.production.view"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        snap = await client.get("/api/dashboard/produksi/snapshot")
        denied_schedule = await client.post("/api/orders/po-1/production-schedule/finalize", json={"action_id": "x"})
        denied_holiday = await client.post("/api/settings/operational-holidays", json={"action_id": "y", "date": "2026-11-01", "note": ""})
    assert snap.status_code == 200
    assert snap.json()["kesiapan"]["status"] == "complete"
    assert snap.json()["wave2"]["target_produksi"]["status"] == "unlocked"
    assert denied_schedule.status_code == 403
    assert denied_holiday.status_code == 403
    after = await db.orders.find_one({"id": "po-1"}, {"_id": 0})
    assert after == before
    assert "delivery_sla_v2" not in after


async def test_finalize_route_is_idempotent_and_conflicts(db):
    await db.orders.insert_one(_order())
    app.dependency_overrides[current_user] = _user(["schedule.edit"])
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.post("/api/orders/po-1/production-schedule/finalize", json={"action_id": "once", "finish_target_date": "2026-10-28"})
        second = await client.post("/api/orders/po-1/production-schedule/finalize", json={"action_id": "once", "finish_target_date": "2026-11-01"})
        third = await client.post("/api/orders/po-1/production-schedule/finalize", json={"action_id": "other", "finish_target_date": "2026-11-01"})
    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["replay"] is True
    assert third.status_code == 409
    saved = await db.orders.find_one({"id": "po-1"}, {"_id": 0})
    assert saved["production_finish_target"]["initial_date"] == "2026-10-28"
    assert saved["operator_splits"][0]["quantity"] == 10000
    kinds = [row["kind"] for row in saved["production_schedule_events"] if row["split_id"] == "s1"]
    assert kinds.count("schedule_finalized") == 1


async def test_two_users_cannot_both_finalize(db):
    await db.orders.insert_one(_order(id="po-race", po_number="PO-RACE"))
    app.dependency_overrides[current_user] = _user(["schedule.edit"])
    transport = httpx.ASGITransport(app=app)

    async def post(action_id):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post(
                "/api/orders/po-race/production-schedule/finalize",
                json={"action_id": action_id, "finish_target_date": "2026-10-28"},
            )

    left, right = await asyncio.gather(post("race-a"), post("race-b"))
    codes = sorted([left.status_code, right.status_code])
    assert codes[0] == 200
    assert codes[1] in (200, 409)
    saved = await db.orders.find_one({"id": "po-race"}, {"_id": 0})
    finals = [row for row in saved["production_schedule_events"] if row["kind"] == "schedule_finalized" and row["split_id"] == "s1"]
    assert len(finals) == 1
    if 409 in (left.status_code, right.status_code):
        assert saved["production_schedule"]["state"] == "finalized"
