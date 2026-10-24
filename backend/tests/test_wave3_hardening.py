"""Wave 3 HTTP guards and the Sunday delivery-plan classification.

Local fixtures only. Does not read or write production data.
"""
from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_wave3_hardening_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from server import app, current_user
from services.delivery_plan import plan_row
from services.production_wave2 import add_working_days, is_working_day, stamp_delivery_sla
from services.tanggal_pengiriman import delivery_sla_effective, resolve_tanggal_pengiriman

pytestmark = pytest.mark.anyio

THU = "2026-10-22T10:00:00+07:00"
SUNDAY = "2026-10-25"


def _user(**perms):
    return {
        "id": "wave3h-user",
        "user_id": "wave3h-user",
        "email": "lead.wave3h@example.com",
        "role": "helper",
        "status": "active",
        "permissions_override": perms,
    }


def _order(**extra):
    row = {
        "id": "wave3h-po",
        "po_number": "PO-W3H",
        "updated_at": "2026-10-22T00:00:00+00:00",
        "current_stage": 5,
        "cancelled": False,
        "priority": "normal",
        "design_status": "Approved",
        "payment_status": "Approved",
        "design_approved_at": THU,
        "payment_approved_at": THU,
        "qty_delivered": 0,
        "buffer_stock": False,
        "line_items": [{
            "name": "Cup A",
            "item_code": "PC-A",
            "quantity": 100,
            "unit": "pcs",
            "production_process": "print",
            "master_item_id": "wave3h-m1",
        }],
        "operator_splits": [],
        "timeline": [],
    }
    row.update(extra)
    return row


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def clean_db():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    await server.db.orders.delete_many({"id": {"$regex": "^wave3h-"}})
    await server.db.branches.delete_many({"id": {"$regex": "^wave3h-"}})
    await server.db.master_items.delete_many({"id": {"$regex": "^wave3h-"}})
    yield
    await server.db.orders.delete_many({"id": {"$regex": "^wave3h-"}})
    await server.db.branches.delete_many({"id": {"$regex": "^wave3h-"}})
    await server.db.master_items.delete_many({"id": {"$regex": "^wave3h-"}})
    app.dependency_overrides.pop(current_user, None)


def test_sunday_rows_are_legacy_calendar_not_the_working_day_contract():
    anchor = date(2026, 10, 22)
    assert anchor.weekday() == 3
    assert is_working_day(date(2026, 10, 24), []) is True
    assert is_working_day(date(2026, 10, 25), []) is False
    assert add_working_days(anchor, 3, []).isoformat() == "2026-10-26"
    assert add_working_days(anchor, 3, ["2026-10-24"]).isoformat() == "2026-10-27"
    assert add_working_days(anchor, 3, ["2026-10-26"]).isoformat() == "2026-10-27"

    legacy = _order(id="wave3h-legacy", po_number="PO-LEGACY")
    fact = delivery_sla_effective(legacy, 3, today=date(2026, 10, 24))
    assert fact["date"] == SUNDAY
    assert fact["contract"] == "legacy_calendar"
    assert fact["source"] == "legacy"
    snap = resolve_tanggal_pengiriman(legacy, 3)
    assert snap["tanggal_pengiriman"] == SUNDAY
    assert snap["source"] == "automatic"
    row = plan_row(legacy, lead_time_days=3, today="2026-10-24")
    assert row["tanggal_pengiriman"] == SUNDAY
    assert row["tanggal_pengiriman_source"] == "automatic"
    assert row["delivery_sla_effective"]["contract"] == "legacy_calendar"

    stamped = _order(id="wave3h-new", po_number="PO-NEW")
    assert stamp_delivery_sla(stamped, []) is True
    assert stamped["delivery_sla_v2"]["automatic_date"] == "2026-10-26"
    new_row = plan_row(stamped, lead_time_days=3, today="2026-10-24")
    assert new_row["tanggal_pengiriman"] == "2026-10-26"
    assert new_row["delivery_sla_effective"]["contract"] == "sla_v2"
    assert new_row["delivery_sla_effective"]["source"] == "otomatis"

    manual = _order(
        id="wave3h-manual",
        delivery_deadline_manual="2026-10-25T00:00:00+07:00",
        scheduled_delivery_date=SUNDAY,
        delivery_sla_v2={"automatic_date": "2026-10-26", "working_days": 3, "unit": "working_days"},
    )
    assert plan_row(manual, today="2026-10-24")["tanggal_pengiriman"] == SUNDAY
    assert delivery_sla_effective(manual)["source"] == "manual"

    sj = _order(
        id="wave3h-sj",
        delivery_sla_v2={"automatic_date": "2026-10-26", "working_days": 3, "unit": "working_days", "legacy": False},
        shipments=[{"id": "sj-1", "delivery_date": SUNDAY, "lines": [{"qty": 1}]}],
    )
    sla = delivery_sla_effective(sj)
    assert sla["date"] == "2026-10-26"
    assert sla["contract"] == "sla_v2"
    assert sj["shipments"][0]["delivery_date"] == SUNDAY

    urgent = _order(id="wave3h-urgent", priority="urgent")
    assert delivery_sla_effective(urgent)["source"] == "belum_dijadwalkan"
    assert plan_row(urgent) is None

    buffer = _order(id="wave3h-buffer", buffer_stock=True, scheduled_delivery_date="2026-10-20")
    assert delivery_sla_effective(buffer, today=date(2026, 10, 24))["state"] == "buffer"
    assert plan_row(buffer, today="2026-10-24")["tanggal_pengiriman"] == "2026-10-20"


@pytest.mark.asyncio
async def test_branch_assignment_http_and_confirmation_ignore_client_branch(clean_db):
    await server.db.branches.insert_many([
        {"id": "wave3h-ygy", "code": "YGY", "name": "Yogyakarta", "active": True},
        {"id": "wave3h-smg", "code": "SMG", "name": "Semarang", "active": True},
        {"id": "wave3h-off", "code": "OFF", "name": "Ditutup", "active": False},
    ])
    await server.db.master_items.insert_many([
        {"id": "wave3h-m1", "item_name": "Cup A", "item_code": "PC-A", "is_active": True, "production_process": "print"},
        {"id": "wave3h-m2", "item_name": "Cup B", "item_code": "PC-B", "is_active": True, "production_process": "print"},
    ])
    await server.db.orders.insert_one(_order())
    app.dependency_overrides[current_user] = lambda: _user(**{
        "schedule.edit": True,
        "operator.update": True,
        "po.edit": True,
        "delivery.schedule.view": True,
    })
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        active = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
            "production_branch_id": "wave3h-ygy",
        }]})
        assert active.status_code == 200, active.text
        split = active.json()["order"]["operator_splits"][0]
        assert split["production_branch_id"] == "wave3h-ygy"

        unknown = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
            "production_branch_id": "wave3h-missing",
        }]})
        assert unknown.status_code == 400
        assert "tidak valid" in str(unknown.json()["detail"])

        inactive = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
            "production_branch_id": "wave3h-off",
        }]})
        assert inactive.status_code == 400

        omitted = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
        }]})
        assert omitted.status_code == 200, omitted.text
        assert omitted.json()["order"]["operator_splits"][0]["production_branch_id"] == "wave3h-ygy"

        blank = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
            "production_branch_id": "",
        }]})
        assert blank.status_code == 200, blank.text
        assert not blank.json()["order"]["operator_splits"][0].get("production_branch_id")

        restored = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
            "production_branch_id": "wave3h-ygy",
        }]})
        assert restored.status_code == 200, restored.text

        ready = await server.db.orders.find_one({"id": "wave3h-po"}, {"_id": 0})
        ready["sample_approved"] = True
        ready["screener_info"] = {"screen_qty": 100}
        ready["helper_prep"] = {"qty_prepared": 100}
        await server.db.orders.replace_one({"id": "wave3h-po"}, ready)
        before = await server.db.orders.find_one({"id": "wave3h-po"}, {"_id": 0, "operator_splits": 1})
        progress = await client.post("/api/orders/wave3h-po/operator-progress", json={
            "operator_id": split["id"],
            "qty": 4,
            "action_id": "wave3h-print",
            "production_branch_id": "evil-branch",
        })
        assert progress.status_code == 200, progress.text
        saved = progress.json()["order"]["operator_splits"][0]
        entry = saved["progress_entries"][0]
        assert entry["production_branch_id"] == "wave3h-ygy"
        assert entry.get("production_branch_id") != "evil-branch"
        assert saved["production_branch_id"] == "wave3h-ygy"

        moved = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
            "production_branch_id": "wave3h-ygy",
        }]})
        assert moved.status_code == 200
        stolen = await client.post("/api/orders/wave3h-po/assign-operators", json={"splits": [{
            "id": split["id"],
            "operator_email": "op.wave3h@example.com",
            "operator_name": "Operator",
            "quantity": 100,
            "line_idx": 0,
            "production_branch_id": "wave3h-smg",
        }]})
        assert stolen.status_code == 400
        assert "tidak dapat dipindah" in str(stolen.json()["detail"])
        kept = await server.db.orders.find_one({"id": "wave3h-po"}, {"_id": 0, "operator_splits": 1})
        assert kept["operator_splits"][0]["production_branch_id"] == "wave3h-ygy"
        assert kept["operator_splits"][0]["progress_entries"][0]["action_id"] == "wave3h-print"
        assert before["operator_splits"][0]["id"] == kept["operator_splits"][0]["id"]

        locked = await client.patch("/api/sales-orders/wave3h-po/lines/0/product", json={
            "master_item_id": "wave3h-m2",
        })
        assert locked.status_code == 409
        assert locked.json()["detail"]["code"] == "CETAK_SUDAH_DIKONFIRMASI"
        still = await server.db.orders.find_one({"id": "wave3h-po"}, {"_id": 0, "line_items": 1})
        assert still["line_items"][0]["master_item_id"] == "wave3h-m1"

        legacy = _order(id="wave3h-plan-legacy", po_number="PO-PLAN-LEGACY")
        stamped = _order(id="wave3h-plan-new", po_number="PO-PLAN-NEW")
        stamp_delivery_sla(stamped, [])
        await server.db.orders.insert_many([legacy, stamped])
        before_legacy = await server.db.orders.find_one({"id": "wave3h-plan-legacy"}, {"_id": 0})
        plan = await client.get("/api/delivery/plan", params={"page_size": 100, "q": "PO-PLAN"})
        assert plan.status_code == 200
        rows = {row["po_number"]: row for row in plan.json()["rows"]}
        assert rows["PO-PLAN-LEGACY"]["tanggal_pengiriman"] == SUNDAY
        assert rows["PO-PLAN-LEGACY"]["delivery_sla_effective"]["contract"] == "legacy_calendar"
        assert rows["PO-PLAN-NEW"]["tanggal_pengiriman"] == "2026-10-26"
        assert rows["PO-PLAN-NEW"]["delivery_sla_effective"]["source"] == "otomatis"
        after_legacy = await server.db.orders.find_one({"id": "wave3h-plan-legacy"}, {"_id": 0})
        before_legacy.pop("_id", None)
        after_legacy.pop("_id", None)
        assert after_legacy == before_legacy
