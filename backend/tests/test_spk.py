"""SPK Antar Divisi — permissions, progress, verification, deadline, concurrency."""
import asyncio
import os
import sys
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_packaging_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import httpx

from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS, effective_permissions
from server import app, current_user
from services import spk as spk_service
from services.spk import (
    EVENTS,
    ORDERS,
    ensure_indexes,
    milestone_progress_percent,
    overall_progress_percent,
)


def _user(role, *, uid=None, name=None, override=None):
    ident = uid or f"{role}-{uuid.uuid4().hex[:8]}"
    return {
        "id": ident,
        "user_id": ident,
        "email": f"{ident}@eternal.test",
        "name": name or role,
        "role": role,
        "permissions_override": override or {},
        "branch_id": "br-sby",
        "branch": "Surabaya",
        "status": "active",
    }


def _deny():
    return {key: False for key in (
        "spk.create", "spk.view_own", "spk.view_division", "spk.view_all",
        "spk.assign", "spk.update_progress", "spk.verify", "spk.cancel", "spk.manage", "nav.spk",
    )}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    import server
    from motor.motor_asyncio import AsyncIOMotorClient
    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]
    spk_service._today_override = None
    spk_service._progress_crash_after = None
    yield
    spk_service._today_override = None
    spk_service._progress_crash_after = None
    app.dependency_overrides.pop(current_user, None)


def _client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _prepare(users):
    import server
    from services.notification_indexes import ensure_indexes as ensure_notification_indexes
    db = server.db
    await ensure_indexes(db)
    await ensure_notification_indexes(db)
    await db[ORDERS].delete_many({})
    await db[EVENTS].delete_many({})
    await db.notifications.delete_many({"event_type": {"$regex": "^spk_"}})
    for user in users:
        await db.users.delete_many({"email": user["email"]})
        await db.users.insert_one(dict(user))
    return db


def _milestones():
    return [
        {"title": "Materi iklan baru", "type": "QUANTITY", "weight": "25", "target_value": "10", "unit": "materi", "requires_evidence": False},
        {"title": "Campaign aktif", "type": "QUANTITY", "weight": "30", "target_value": "5", "unit": "campaign", "requires_evidence": True},
        {"title": "Lead masuk", "type": "QUANTITY", "weight": "25", "target_value": "100", "unit": "lead", "requires_evidence": False},
        {"title": "Laporan evaluasi", "type": "CHECKLIST", "weight": "20", "requires_evidence": False},
    ]


def _payload(pic_id, **extra):
    body = {
        "title": "Perbanyak aktivitas iklan Eternal",
        "instruction": "Naikkan materi, campaign, dan lead masuk sebelum 7 September.",
        "priority": "HIGH",
        "target_division": "penjualan",
        "assigned_user_ids": [pic_id],
        "start_date": "2026-09-01",
        "deadline": "2026-09-07",
        "milestones": _milestones(),
    }
    body.update(extra)
    return body


def test_catalog_and_builtin_role_defaults_do_not_touch_holding():
    for key in (
        "spk.create", "spk.view_own", "spk.view_division", "spk.view_all",
        "spk.assign", "spk.update_progress", "spk.verify", "spk.cancel", "spk.manage", "nav.spk",
    ):
        assert key in ALL_PERMISSIONS
    direktur = set(ROLE_DEFAULT_PERMISSIONS["direktur_utama"])
    assert {"spk.view_all", "spk.create", "spk.assign", "spk.verify", "spk.cancel", "nav.spk"} <= direktur
    assert "spk.update_progress" not in direktur
    head = set(ROLE_DEFAULT_PERMISSIONS["head_production"])
    assert {"spk.view_division", "spk.assign", "spk.update_progress", "spk.verify", "nav.spk"} <= head
    assert "spk.view_all" not in head
    staff = set(ROLE_DEFAULT_PERMISSIONS["crm"])
    assert {"spk.view_own", "spk.update_progress", "nav.spk"} <= staff
    assert "spk.verify" not in staff
    assert "spk.view_all" not in staff
    holding = set(ROLE_DEFAULT_PERMISSIONS["holding_team"])
    assert "spk.view_all" not in holding
    assert "spk.create" not in holding
    customized = effective_permissions("direktur_utama", None, {"direktur_utama": ["product_request.read_all"]})
    assert "spk.view_all" not in customized
    assert "product_request.read_all" in customized


def test_progress_math_caps_at_100_and_mixes_types():
    rows = [
        {"type": "QUANTITY", "weight": "25", "target_value": "10", "actual_value": "6", "status": "IN_PROGRESS"},
        {"type": "QUANTITY", "weight": "25", "target_value": "5", "actual_value": "3", "status": "IN_PROGRESS"},
        {"type": "QUANTITY", "weight": "25", "target_value": "100", "actual_value": "42", "status": "IN_PROGRESS"},
        {"type": "CHECKLIST", "weight": "25", "target_value": "1", "actual_value": "0", "status": "PENDING"},
    ]
    assert milestone_progress_percent(rows[0]) == Decimal("60.00")
    assert milestone_progress_percent(rows[1]) == Decimal("60.00")
    assert milestone_progress_percent(rows[2]) == Decimal("42.00")
    assert milestone_progress_percent(rows[3]) == Decimal("0.00")
    assert overall_progress_percent(rows) == Decimal("40.50")
    rows[1]["actual_value"] = "50"
    assert milestone_progress_percent(rows[1]) == Decimal("100.00")
    rows[3]["status"] = "COMPLETED"
    assert milestone_progress_percent(rows[3]) == Decimal("100.00")
    approved = {"type": "APPROVAL", "weight": "100", "target_value": "1", "actual_value": "0", "status": "PENDING"}
    assert milestone_progress_percent(approved) == Decimal("0.00")
    approved["status"] = "COMPLETED"
    assert overall_progress_percent([approved]) == Decimal("100.00")
    huge = {"type": "QUANTITY", "weight": "100", "target_value": "1", "actual_value": "999", "status": "COMPLETED"}
    assert overall_progress_percent([huge]) == Decimal("100.00")


def test_deadline_state_is_derived():
    assert spk_service.deadline_state("COMPLETED", "2026-09-01", date(2026, 9, 8)) is None
    assert spk_service.deadline_state("CANCELLED", "2026-09-01", date(2026, 9, 8)) is None
    assert spk_service.deadline_state("IN_PROGRESS", "2026-09-11", date(2026, 9, 7)) == "ON_TRACK"
    assert spk_service.deadline_state("IN_PROGRESS", "2026-09-10", date(2026, 9, 7)) == "DUE_SOON"
    assert spk_service.deadline_state("IN_PROGRESS", "2026-09-07", date(2026, 9, 7)) == "DUE_SOON"
    assert spk_service.deadline_state("IN_PROGRESS", "2026-09-06", date(2026, 9, 7)) == "OVERDUE"


@pytest.mark.anyio
async def test_permissions_hide_cross_division_and_block_strangers():
    director = _user("direktur_utama", name="Dimas", override={
        "spk.view_all": True, "spk.create": True, "spk.assign": True, "spk.verify": True, "spk.cancel": True,
    })
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    stranger = _user("holding_team", name="Tamu", override=_deny())
    other = _user("operator", name="Operator Lain", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic, stranger, other])
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True})
        assert created.status_code == 200, created.text
        order = created.json()["order"]
        assert order["spk_number"].startswith("SPK-")
        assert order["workflow_status"] == "PUBLISHED"
        assert order["source_division"] == "direksi"
        assert order["branch_name"] == "Surabaya"
    async with _client_for(stranger) as client:
        denied = await client.get("/api/spk")
        assert denied.status_code == 403
        hidden = await client.get(f"/api/spk/{order['id']}")
        assert hidden.status_code == 403
    async with _client_for(other) as client:
        listing = await client.get("/api/spk", params={"tab": "all"})
        assert listing.status_code == 200
        assert listing.json()["orders"] == []
        missing = await client.get(f"/api/spk/{order['id']}")
        assert missing.status_code == 404
        blocked = await client.post(f"/api/spk/{order['id']}/progress", json={"doc_version": 1, "milestone_id": "x", "new_actual": 1})
        assert blocked.status_code == 404
    async with _client_for(pic) as client:
        mine = await client.get("/api/spk", params={"tab": "mine"})
        assert mine.status_code == 200
        assert any(row["id"] == order["id"] for row in mine.json()["orders"])
        assert "all" not in mine.json()["tabs"]
        assert "division" not in mine.json()["tabs"]


@pytest.mark.anyio
async def test_publish_requires_target_pic_deadline_milestone_and_unique_numbers():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    async with _client_for(director) as client:
        draft = await client.post("/api/spk", json={"title": "Draft iklan", "priority": "LOW"})
        assert draft.status_code == 200, draft.text
        assert draft.json()["order"]["workflow_status"] == "DRAFT"
        bad = await client.post("/api/spk", json={"title": "Tanpa milestone", "publish": True, "target_division": "penjualan", "assigned_user_ids": [pic["id"]], "deadline": "2026-09-07", "start_date": "2026-09-01", "instruction": "Kerjakan"})
        assert bad.status_code == 400
        uneven = _payload(pic["id"], publish=True)
        uneven["milestones"] = uneven["milestones"][:2]
        uneven_res = await client.post("/api/spk", json=uneven)
        assert uneven_res.status_code == 400
        first = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True})
        second = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True, "title": "SPK kedua"})
        assert first.status_code == 200 and second.status_code == 200
        import re
        for body in (first.json()["order"], second.json()["order"]):
            assert re.fullmatch(r"SPK-\d{6}-\d{4}", body["spk_number"])
        assert first.json()["order"]["spk_number"] != second.json()["order"]["spk_number"]


@pytest.mark.anyio
async def test_progress_append_evidence_verification_and_history():
    director = _user("direktur_utama", name="Dimas", override={
        "spk.view_all": True, "spk.create": True, "spk.verify": True, "spk.cancel": True,
    })
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True, "spk.verify": True})
    await _prepare([director, pic])
    spk_service._today_override = date(2026, 9, 5)
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True})
        order = created.json()["order"]
        assert order["deadline_state"] == "DUE_SOON"
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": order["doc_version"]})
        assert ack.status_code == 200, ack.text
        started = await client.post(f"/api/spk/{order['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        assert started.status_code == 200, started.text
        current = started.json()["order"]
        campaign = next(row for row in current["milestones"] if row["title"] == "Campaign aktif")
        missing = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": current["doc_version"],
            "milestone_id": campaign["id"],
            "new_actual": 5,
            "idempotency_key": "need-evidence",
        })
        assert missing.status_code == 400
        saved = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": current["doc_version"],
            "milestone_id": campaign["id"],
            "new_actual": 3,
            "note": "Tiga campaign hidup",
            "links": [{"url": "https://ads.example/campaign", "label": "Dashboard iklan"}],
            "idempotency_key": "progress-1",
        })
        assert saved.status_code == 200, saved.text
        assert saved.json()["replayed"] is False
        after = saved.json()["order"]
        updated = next(row for row in after["milestones"] if row["id"] == campaign["id"])
        assert updated["actual_value"] == "3.00"
        assert Decimal(after["progress_percent"]) == Decimal("18.00")
        replay = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": after["doc_version"],
            "milestone_id": campaign["id"],
            "new_actual": 9,
            "idempotency_key": "progress-1",
        })
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"] is True
        assert replay.json()["order"]["progress_percent"] == after["progress_percent"]
        stale = await client.post(f"/api/spk/{order['id']}/progress", json={
            "doc_version": current["doc_version"],
            "milestone_id": campaign["id"],
            "new_actual": 4,
            "idempotency_key": "progress-stale",
        })
        assert stale.status_code == 409
        self_approve = await client.post(f"/api/spk/{order['id']}/verify", json={"decision": "approve", "doc_version": after["doc_version"]})
        assert self_approve.status_code == 403
    import server
    events = await server.db[EVENTS].find({"spk_id": order["id"], "event_type": "PROGRESS_UPDATE"}).to_list(20)
    assert len(events) == 1
    assert events[0]["previous_actual"] == "0.00"
    assert events[0]["new_actual"] == "3.00"

    async with _client_for(pic) as client:
        detail = (await client.get(f"/api/spk/{order['id']}")).json()["order"]
        for row in detail["milestones"]:
            if row["status"] == "COMPLETED":
                continue
            version = detail["doc_version"]
            body = {
                "doc_version": version,
                "milestone_id": row["id"],
                "idempotency_key": f"finish-{row['id']}",
                "links": [{"url": "https://ads.example/bukti", "label": "Bukti"}],
            }
            if row["type"] == "QUANTITY":
                body["new_actual"] = row["target_value"]
            elif row["type"] == "CHECKLIST":
                body["complete"] = True
            else:
                body["approve"] = True
            res = await client.post(f"/api/spk/{order['id']}/progress", json=body)
            assert res.status_code == 200, res.text
            detail = res.json()["order"]
        requested = await client.post(f"/api/spk/{order['id']}/verification-request", json={"doc_version": detail["doc_version"]})
        assert requested.status_code == 200, requested.text
        assert requested.json()["order"]["workflow_status"] == "WAITING_VERIFICATION"
        waiting = requested.json()["order"]
    async with _client_for(director) as client:
        revised = await client.post(f"/api/spk/{order['id']}/verify", json={
            "decision": "revision",
            "reason": "Tambahkan TikTok Ads",
            "doc_version": waiting["doc_version"],
        })
        assert revised.status_code == 200, revised.text
        assert revised.json()["order"]["workflow_status"] == "REVISION_REQUIRED"
        kept = next(row for row in revised.json()["order"]["milestones"] if row["title"] == "Campaign aktif")
        assert kept["actual_value"] == "5.00"
    import server
    history = await server.db[EVENTS].count_documents({"spk_id": order["id"], "event_type": "PROGRESS_UPDATE"})
    assert history >= 2
    async with _client_for(pic) as client:
        detail = (await client.get(f"/api/spk/{order['id']}")).json()["order"]
        again = await client.post(f"/api/spk/{order['id']}/verification-request", json={"doc_version": detail["doc_version"]})
        assert again.status_code == 200, again.text
        waiting = again.json()["order"]
    async with _client_for(director) as client:
        approved = await client.post(f"/api/spk/{order['id']}/verify", json={
            "decision": "approve",
            "doc_version": waiting["doc_version"],
        })
        assert approved.status_code == 200, approved.text
        done = approved.json()["order"]
        assert done["workflow_status"] == "COMPLETED"
        assert done["deadline_state"] is None
        assert Decimal(done["progress_percent"]) == Decimal("100.00")
    notes = await server.db.notifications.find({"event_type": {"$regex": "^spk_"}, "user_id": pic["id"]}).to_list(20)
    kinds = {row["event_type"] for row in notes}
    assert "spk_assigned" in kinds
    assert "spk_completed" in kinds


@pytest.mark.anyio
async def test_parallel_progress_does_not_drop_the_winner():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True})
        order = created.json()["order"]
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": order["doc_version"]})
        started = await client.post(f"/api/spk/{order['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        current = started.json()["order"]
        lead = next(row for row in current["milestones"] if row["title"] == "Lead masuk")
        version = current["doc_version"]

        async def _hit(key, actual):
            async with _client_for(pic) as inner:
                return await inner.post(f"/api/spk/{order['id']}/progress", json={
                    "doc_version": version,
                    "milestone_id": lead["id"],
                    "new_actual": actual,
                    "idempotency_key": key,
                })

        first, second = await asyncio.gather(_hit("race-a", 10), _hit("race-b", 20))
    codes = sorted([first.status_code, second.status_code])
    assert codes == [200, 409], (first.text, second.text)
    import server
    stored = await server.db[ORDERS].find_one({"id": order["id"]}, {"_id": 0})
    actual = next(row["actual_value"] for row in stored["milestones"] if row["id"] == lead["id"])
    assert actual in ("10.00", "20.00")
    winners = await server.db[EVENTS].count_documents({
        "spk_id": order["id"],
        "event_type": "PROGRESS_UPDATE",
        "commit_state": "applied",
    })
    assert winners == 1


@pytest.mark.anyio
async def test_overdue_list_state_and_draft_delete_not_published():
    director = _user("direktur_utama", name="Dimas", override={
        "spk.view_all": True, "spk.create": True, "spk.cancel": True,
    })
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True})
    await _prepare([director, pic])
    spk_service._today_override = date(2026, 9, 8)
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True})
        order = created.json()["order"]
        listing = await client.get("/api/spk", params={"tab": "overdue"})
        assert listing.status_code == 200
        row = next(item for item in listing.json()["orders"] if item["id"] == order["id"])
        assert row["deadline_state"] == "OVERDUE"
        assert row["workflow_status"] == "PUBLISHED"
        removed = await client.delete(f"/api/spk/{order['id']}")
        assert removed.status_code == 400
        cancelled = await client.post(f"/api/spk/{order['id']}/cancel", json={"reason": "Arahan berubah", "doc_version": order["doc_version"]})
        assert cancelled.status_code == 200
        assert cancelled.json()["order"]["workflow_status"] == "CANCELLED"
        draft = await client.post("/api/spk", json={"title": "Draft dibuang"})
        deleted = await client.delete(f"/api/spk/{draft.json()['order']['id']}")
        assert deleted.status_code == 200


async def _snap(db):
    orders = await db[ORDERS].find({}, {
        "_id": 0,
        "id": 1,
        "doc_version": 1,
        "updated_at": 1,
        "workflow_status": 1,
        "progress_percent": 1,
        "deadline": 1,
    }).to_list(100)
    return {
        "orders": sorted(orders, key=lambda row: row["id"]),
        "events": await db[EVENTS].count_documents({}),
        "notifications": await db.notifications.count_documents({}),
        "deadline_notifications": await db.notifications.count_documents({
            "event_type": {"$in": ["spk_due_soon", "spk_overdue"]},
        }),
    }


@pytest.mark.anyio
async def test_get_list_and_detail_are_zero_write():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    spk_service._today_override = date(2026, 9, 22)
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True})
        assert created.status_code == 200, created.text
        order = created.json()["order"]
    import server
    before = await _snap(server.db)
    async with _client_for(director) as client:
        listing = await client.get("/api/spk", params={"tab": "all", "deadline_state": "OVERDUE"})
        detail = await client.get(f"/api/spk/{order['id']}")
        events = await client.get(f"/api/spk/{order['id']}/events")
        assert listing.status_code == 200
        assert detail.status_code == 200
        assert events.status_code == 200
        row = next(item for item in listing.json()["orders"] if item["id"] == order["id"])
        assert row["deadline_state"] == "OVERDUE"
        assert row["workflow_status"] == "PUBLISHED"
        assert listing.json()["summary"]["overdue"] >= 1
        assert detail.json()["order"]["deadline_state"] == "OVERDUE"
    after = await _snap(server.db)
    assert after == before
    assert after["deadline_notifications"] == 0


@pytest.mark.anyio
async def test_spk_number_is_monthly_and_unique_under_concurrency():
    import re
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True})
    await _prepare([director, pic])
    spk_service._today_override = date(2026, 9, 22)

    async def _create(title):
        async with _client_for(director) as client:
            res = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True, "title": title})
            assert res.status_code == 200, res.text
            return res.json()["order"]["spk_number"]

    first, second = await asyncio.gather(_create("SPK September A"), _create("SPK September B"))
    assert {first, second} == {"SPK-202609-0001", "SPK-202609-0002"}
    assert re.fullmatch(r"SPK-202609-\d{4}", first)
    spk_service._today_override = date(2026, 10, 3)
    october = await _create("SPK Oktober")
    assert october == "SPK-202610-0001"


async def _started(director, pic):
    async with _client_for(director) as client:
        created = await client.post("/api/spk", json={**_payload(pic["id"]), "publish": True})
        assert created.status_code == 200, created.text
        order = created.json()["order"]
    async with _client_for(pic) as client:
        ack = await client.post(f"/api/spk/{order['id']}/acknowledge", json={"doc_version": order["doc_version"]})
        assert ack.status_code == 200, ack.text
        started = await client.post(f"/api/spk/{order['id']}/start", json={"doc_version": ack.json()["order"]["doc_version"]})
        assert started.status_code == 200, started.text
        return started.json()["order"]


def _pic_perms():
    return {"spk.view_own", "spk.update_progress"}


@pytest.mark.anyio
async def test_progress_crash_windows_converge_and_notifications_dedupe():
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    import server
    db = server.db
    order = await _started(director, pic)
    lead = next(row for row in order["milestones"] if row["title"] == "Lead masuk")
    version = order["doc_version"]
    base_actual = next(row["actual_value"] for row in order["milestones"] if row["id"] == lead["id"])

    reserve_body = {
        "doc_version": version,
        "milestone_id": lead["id"],
        "new_actual": 42,
        "idempotency_key": "crash-reserve",
    }
    spk_service._progress_crash_after = "reserve"
    try:
        await spk_service.record_progress(db, order["id"], reserve_body, pic, _pic_perms())
        raise AssertionError("expected reserve crash")
    except spk_service.ProgressCrash:
        pass
    finally:
        spk_service._progress_crash_after = None
    parked = await db[ORDERS].find_one({"id": order["id"]}, {"_id": 0})
    assert next(row["actual_value"] for row in parked["milestones"] if row["id"] == lead["id"]) == "0.00"
    assert parked["doc_version"] == version
    assert await db[EVENTS].count_documents({"idempotency_key": "crash-reserve", "commit_state": "reserved"}) == 1
    async with _client_for(pic) as client:
        quiet = await client.get(f"/api/spk/{order['id']}/events")
        assert quiet.status_code == 200
        assert all(row["event_type"] != "PROGRESS_UPDATE" for row in quiet.json()["events"])
    await spk_service.record_progress(db, order["id"], reserve_body, pic, _pic_perms())
    stored = await db[ORDERS].find_one({"id": order["id"]}, {"_id": 0})
    actual = next(row["actual_value"] for row in stored["milestones"] if row["id"] == lead["id"])
    assert base_actual == "0.00"
    assert actual == "42.00"
    assert stored["doc_version"] == version + 1
    reserved_left = await db[EVENTS].count_documents({"idempotency_key": "crash-reserve", "commit_state": "reserved"})
    applied = await db[EVENTS].find_one({"idempotency_key": "crash-reserve", "commit_state": "applied"}, {"_id": 0})
    assert reserved_left == 0
    assert applied["new_actual"] == actual
    assert applied["overall_after"] == stored["progress_percent"]
    replay = await spk_service.record_progress(db, order["id"], {**reserve_body, "new_actual": 99, "doc_version": 1}, pic, _pic_perms())
    assert replay["replayed"] is True
    again = await db[ORDERS].find_one({"id": order["id"]}, {"_id": 0})
    assert again["doc_version"] == stored["doc_version"]
    assert next(row["actual_value"] for row in again["milestones"] if row["id"] == lead["id"]) == "42.00"
    assert await db[EVENTS].count_documents({"spk_id": order["id"], "event_type": "PROGRESS_UPDATE", "commit_state": "applied"}) == 1

    mutate_order = await _started(director, pic)
    mutate_lead = next(row for row in mutate_order["milestones"] if row["title"] == "Lead masuk")
    mutate_body = {
        "doc_version": mutate_order["doc_version"],
        "milestone_id": mutate_lead["id"],
        "new_actual": 11,
        "idempotency_key": "crash-mutate",
    }
    spk_service._progress_crash_after = "mutate"
    try:
        await spk_service.record_progress(db, mutate_order["id"], mutate_body, pic, _pic_perms())
        raise AssertionError("expected mutate crash")
    except spk_service.ProgressCrash:
        pass
    finally:
        spk_service._progress_crash_after = None
    mid = await db[ORDERS].find_one({"id": mutate_order["id"]}, {"_id": 0})
    assert next(row["actual_value"] for row in mid["milestones"] if row["id"] == mutate_lead["id"]) == "11.00"
    assert mid["doc_version"] == mutate_order["doc_version"] + 1
    assert await db[EVENTS].count_documents({"idempotency_key": "crash-mutate", "commit_state": "applied"}) == 0
    assert await db[EVENTS].count_documents({"idempotency_key": "crash-mutate", "commit_state": "reserved"}) == 1
    recovered = await spk_service.record_progress(db, mutate_order["id"], mutate_body, pic, _pic_perms())
    final = await db[ORDERS].find_one({"id": mutate_order["id"]}, {"_id": 0})
    assert final["doc_version"] == mid["doc_version"]
    assert next(row["actual_value"] for row in final["milestones"] if row["id"] == mutate_lead["id"]) == "11.00"
    canonical = await db[EVENTS].find_one({"idempotency_key": "crash-mutate", "commit_state": "applied"}, {"_id": 0})
    assert canonical["new_actual"] == "11.00"
    assert canonical["previous_actual"] == "0.00"
    assert canonical["overall_after"] == final["progress_percent"]
    assert recovered["event"]["new_actual"] == "11.00"

    note_order = await _started(director, pic)
    note_lead = next(row for row in note_order["milestones"] if row["title"] == "Lead masuk")
    note_body = {
        "doc_version": note_order["doc_version"],
        "milestone_id": note_lead["id"],
        "new_actual": 7,
        "idempotency_key": "crash-notify",
    }
    before_notes = await db.notifications.count_documents({"event_type": "spk_progress_updated", "user_id": director["id"]})
    spk_service._progress_crash_after = "canonical"
    try:
        await spk_service.record_progress(db, note_order["id"], note_body, pic, _pic_perms())
        raise AssertionError("expected canonical crash")
    except spk_service.ProgressCrash:
        pass
    finally:
        spk_service._progress_crash_after = None
    assert await db.notifications.count_documents({
        "event_type": "spk_progress_updated",
        "order_id": note_order["id"],
    }) == 0
    first = await spk_service.record_progress(db, note_order["id"], note_body, pic, _pic_perms())
    second = await spk_service.record_progress(db, note_order["id"], {**note_body, "new_actual": 70}, pic, _pic_perms())
    assert first["replayed"] is True
    assert second["replayed"] is True
    assert second["order"]["progress_percent"] == first["order"]["progress_percent"]
    notes = await db.notifications.count_documents({
        "event_type": "spk_progress_updated",
        "order_id": note_order["id"],
        "user_id": director["id"],
    })
    assert notes == 1
    assert await db.notifications.count_documents({"event_type": "spk_progress_updated", "user_id": director["id"]}) == before_notes + 1
    saved = await db[ORDERS].find_one({"id": note_order["id"]}, {"_id": 0})
    assert next(row["actual_value"] for row in saved["milestones"] if row["id"] == note_lead["id"]) == "7.00"
    assert saved["doc_version"] == note_order["doc_version"] + 1


@pytest.mark.anyio
async def test_missing_evidence_does_not_finalize_progress():
    from fastapi import HTTPException
    director = _user("direktur_utama", name="Dimas", override={"spk.view_all": True, "spk.create": True})
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    import server
    db = server.db
    order = await _started(director, pic)
    campaign = next(row for row in order["milestones"] if row["title"] == "Campaign aktif")
    with pytest.raises(HTTPException) as missing:
        await spk_service.record_progress(db, order["id"], {
            "doc_version": order["doc_version"],
            "milestone_id": campaign["id"],
            "new_actual": 5,
            "file_ids": ["file-does-not-exist"],
            "idempotency_key": "missing-file",
        }, pic, _pic_perms())
    assert missing.value.status_code == 400
    untouched = await db[ORDERS].find_one({"id": order["id"]}, {"_id": 0})
    assert untouched["doc_version"] == order["doc_version"]
    assert next(row["actual_value"] for row in untouched["milestones"] if row["id"] == campaign["id"]) == "0.00"
    assert await db[EVENTS].count_documents({"idempotency_key": "missing-file"}) == 0

    file_id = str(uuid.uuid4())
    await db.files.insert_one({
        "id": file_id,
        "kind": "spk",
        "uploaded_by_id": pic["id"],
        "original_filename": "bukti.pdf",
        "content_type": "application/pdf",
        "is_deleted": False,
    })
    lead = next(row for row in order["milestones"] if row["title"] == "Lead masuk")
    body = {
        "doc_version": order["doc_version"],
        "milestone_id": lead["id"],
        "new_actual": 4,
        "file_ids": [file_id],
        "idempotency_key": "file-disappears",
    }
    spk_service._progress_crash_after = "reserve"
    try:
        await spk_service.record_progress(db, order["id"], body, pic, _pic_perms())
        raise AssertionError("expected reserve crash")
    except spk_service.ProgressCrash:
        pass
    finally:
        spk_service._progress_crash_after = None
    await db.files.delete_one({"id": file_id})
    with pytest.raises(HTTPException) as gone:
        await spk_service.record_progress(db, order["id"], body, pic, _pic_perms())
    assert gone.value.status_code == 400
    with pytest.raises(HTTPException) as replayed:
        await spk_service.record_progress(db, order["id"], body, pic, _pic_perms())
    assert replayed.value.status_code == 400
    still = await db[ORDERS].find_one({"id": order["id"]}, {"_id": 0})
    assert still["doc_version"] == order["doc_version"]
    assert next(row["actual_value"] for row in still["milestones"] if row["id"] == lead["id"]) == "0.00"
    rejected = await db[EVENTS].find_one({"idempotency_key": "file-disappears"}, {"_id": 0})
    assert rejected["commit_state"] == "rejected"
    assert rejected["evidence"] == []
    assert await db[EVENTS].count_documents({"spk_id": order["id"], "event_type": "PROGRESS_UPDATE", "commit_state": "applied"}) == 0


@pytest.mark.anyio
async def test_lifecycle_events_match_committed_status():
    director = _user("direktur_utama", name="Dimas", override={
        "spk.view_all": True, "spk.create": True, "spk.cancel": True,
    })
    pic = _user("crm", name="Fahdo", override={"spk.view_own": True, "spk.update_progress": True})
    await _prepare([director, pic])
    order = await _started(director, pic)
    async with _client_for(director) as client:
        cancelled = await client.post(f"/api/spk/{order['id']}/cancel", json={
            "reason": "Arahan dibatalkan",
            "doc_version": order["doc_version"],
        })
        assert cancelled.status_code == 200, cancelled.text
    import server
    stored = await server.db[ORDERS].find_one({"id": order["id"]}, {"_id": 0})
    events = await server.db[EVENTS].find({"spk_id": order["id"]}, {"_id": 0}).to_list(20)
    expected = {
        "PUBLISH": "PUBLISHED",
        "ACKNOWLEDGE": "ACKNOWLEDGED",
        "START": "IN_PROGRESS",
        "CANCEL": "CANCELLED",
    }
    seen = {row["event_type"]: row for row in events}
    for kind, status in expected.items():
        assert seen[kind]["workflow_status_after"] == status
    assert stored["workflow_status"] == "CANCELLED"
    assert seen["CANCEL"]["workflow_status_after"] == stored["workflow_status"]
