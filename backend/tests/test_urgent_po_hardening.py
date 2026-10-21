"""Urgent PO Phase A — targeted hardening (crash recovery + notifications)."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_urgent_po_hardening_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")

import server
from permissions import DIRECTOR_MUST_OPT_IN_PERMISSIONS, director_full_permission_set
from server import app, current_user, user_permissions
from services import urgent_requests as urg
from services.notification_indexes import ensure_indexes as ensure_notification_indexes
from services.notifications import COLLECTION as NOTIF_COLLECTION


PREFIX = "TEST-URG-H"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def bind_db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    await client.drop_database(database.name)
    server.client = client
    server.db = database
    await urg.ensure_indexes(database)
    await ensure_notification_indexes(database)
    yield database
    app.dependency_overrides.pop(current_user, None)
    await client.drop_database(database.name)
    client.close()


def _user(role, **extra):
    return {
        "id": extra.get("id", str(uuid.uuid4())),
        "email": extra.get("email", f"{role}-{uuid.uuid4().hex[:6]}@test.local"),
        "name": extra.get("name", role),
        "role": role,
        "permissions_override": extra.get("permissions_override") or {},
    }


async def _seed_order(db, *, priority="normal", cancelled=False, extra=None):
    now = datetime.now(timezone.utc).isoformat()
    oid = str(uuid.uuid4())
    doc = {
        "id": oid,
        "po_number": f"{PREFIX}-{uuid.uuid4().hex[:6].upper()}",
        "customer": "Hardening",
        "current_stage": 2,
        "priority": priority,
        "urgency_approved_by": "legacy@eternal.local" if priority == "urgent" else None,
        "cancelled": cancelled,
        "created_at": now,
        "updated_at": now,
        "timeline": [],
        "line_items": [],
        "promised_date": "2026-11-15",
        "hpp_snapshot": {"total": 1000},
        "stock_reserved": 5,
        "loading_status": "none",
        "surat_jalan_number": None,
    }
    if extra:
        doc.update(extra)
    await db.orders.insert_one(dict(doc))
    return doc


async def _submit(client, order_id, user):
    app.dependency_overrides[current_user] = lambda: user
    r = await client.post(
        f"/api/orders/{order_id}/urgent-requests",
        json={"reason": "Hardening reason", "target_completion_date": "2026-11-01"},
    )
    assert r.status_code == 200, r.text
    return r.json()["request"]


def test_director_must_opt_in_all_urgent_phase_a():
    for key in (
        "urgent.request",
        "urgent.review",
        "urgent.approve",
        "urgent.notify",
        "urgent.email_notify",
    ):
        assert key in DIRECTOR_MUST_OPT_IN_PERMISSIONS
    full = director_full_permission_set()
    for key in (
        "urgent.request",
        "urgent.review",
        "urgent.approve",
        "urgent.notify",
        "urgent.email_notify",
    ):
        assert key not in full


@pytest.mark.anyio
async def test_crash_before_order_update_same_actor_resumes(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", permissions_override={"urgent.request": True})
    approver = _user("crm", email="apr@t.local", permissions_override={"urgent.approve": True})
    await bind_db.users.insert_one({**requester, "status": "active"})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        # Simulate crash after claim, before order update
        claim_id = str(uuid.uuid4())
        await bind_db[urg.COLLECTION].update_one(
            {"id": req["id"]},
            {
                "$set": {
                    "status": urg.STATUS_APPROVING,
                    "decision_claim_id": claim_id,
                    "decided_by_id": approver["id"],
                    "decided_by_email": approver["email"],
                    "decided_by_name": approver["name"],
                    "decision_notes": "partial",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "resume"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["request"]["status"] == "approved"
        assert r.json()["order"]["priority"] == "urgent"
        ord_doc = await bind_db.orders.find_one({"id": order["id"]}, {"_id": 0})
        assert sum(1 for t in ord_doc.get("timeline") or [] if t.get("urgent_request_id") == req["id"]) == 1


@pytest.mark.anyio
async def test_crash_after_order_update_any_approver_finalizes(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", permissions_override={"urgent.request": True})
    a1 = _user("crm", email="a1@t.local", id="a1", permissions_override={"urgent.approve": True})
    a2 = _user("crm", email="a2@t.local", id="a2", permissions_override={"urgent.approve": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        claim_id = str(uuid.uuid4())
        # Order already urgent + timeline, request still approving (crash before finalize)
        await bind_db.orders.update_one(
            {"id": order["id"]},
            {
                "$set": {
                    "priority": "urgent",
                    "urgency_approved_by": a1["email"],
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
                "$push": {
                    "timeline": {
                        "stage": 2,
                        "stage_label": "Urgent Approval",
                        "urgent_request_id": req["id"],
                        "note": "partial",
                    }
                },
            },
        )
        await bind_db[urg.COLLECTION].update_one(
            {"id": req["id"]},
            {
                "$set": {
                    "status": urg.STATUS_APPROVING,
                    "decision_claim_id": claim_id,
                    "decided_by_id": a1["id"],
                    "decided_by_email": a1["email"],
                    "decided_by_name": a1["name"],
                    "decision_notes": "from a1",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        # Different approver completes finalize (no duplicate timeline)
        app.dependency_overrides[current_user] = lambda: a2
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "a2 finish"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["request"]["status"] == "approved"
        assert body["order"]["priority"] == "urgent"
        assert body["request"]["decided_by_email"] == a1["email"]
        ord_doc = await bind_db.orders.find_one({"id": order["id"]}, {"_id": 0})
        assert sum(1 for t in ord_doc.get("timeline") or [] if t.get("urgent_request_id") == req["id"]) == 1
        hist = [h for h in body["request"].get("history") or [] if h.get("action") == "approved"]
        assert len(hist) == 1


@pytest.mark.anyio
async def test_stale_claim_other_approver_reclaims(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", permissions_override={"urgent.request": True})
    a1 = _user("crm", email="old@t.local", permissions_override={"urgent.approve": True})
    a2 = _user("crm", email="new@t.local", permissions_override={"urgent.approve": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        stale_at = (datetime.now(timezone.utc) - timedelta(seconds=urg.CLAIM_STALE_SECONDS + 30)).isoformat()
        await bind_db[urg.COLLECTION].update_one(
            {"id": req["id"]},
            {
                "$set": {
                    "status": urg.STATUS_APPROVING,
                    "decision_claim_id": str(uuid.uuid4()),
                    "decided_by_email": a1["email"],
                    "decided_by_id": a1["id"],
                    "updated_at": stale_at,
                }
            },
        )
        # Fresh claim by other actor → 409
        fresh_at = datetime.now(timezone.utc).isoformat()
        await bind_db[urg.COLLECTION].update_one(
            {"id": req["id"]},
            {"$set": {"updated_at": fresh_at}},
        )
        app.dependency_overrides[current_user] = lambda: a2
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "too soon"},
        )
        assert r.status_code == 409

        # Make stale again → reclaim succeeds
        await bind_db[urg.COLLECTION].update_one(
            {"id": req["id"]},
            {"$set": {"updated_at": stale_at, "status": urg.STATUS_APPROVING}},
        )
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "reclaim"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["request"]["status"] == "approved"
        assert r.json()["request"]["decided_by_email"] == a2["email"]
        assert r.json()["order"]["priority"] == "urgent"


@pytest.mark.anyio
async def test_concurrent_approve_reject_and_no_approved_normal(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", permissions_override={"urgent.request": True})
    a1 = _user("crm", email="win@t.local", permissions_override={"urgent.approve": True})
    a2 = _user("crm", email="lose@t.local", permissions_override={"urgent.approve": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        app.dependency_overrides[current_user] = lambda: a1
        r1 = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "ok"},
        )
        app.dependency_overrides[current_user] = lambda: a2
        r2 = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "rejected", "decision_notes": "too late"},
        )
        assert r1.status_code == 200
        assert r2.status_code == 409
        final = await bind_db[urg.COLLECTION].find_one({"id": req["id"]}, {"_id": 0})
        ord_doc = await bind_db.orders.find_one({"id": order["id"]}, {"_id": 0})
        assert final["status"] == "approved"
        assert ord_doc["priority"] == "urgent"
        # Side fields untouched by approval
        assert ord_doc.get("promised_date") == "2026-11-15"
        assert ord_doc.get("hpp_snapshot") == {"total": 1000}
        assert ord_doc.get("stock_reserved") == 5
        assert ord_doc.get("loading_status") == "none"
        assert ord_doc.get("surat_jalan_number") is None


@pytest.mark.anyio
async def test_approved_without_urgent_heals_on_retry(bind_db):
    order = await _seed_order(bind_db)
    approver = _user("crm", email="heal@t.local", permissions_override={"urgent.approve": True})
    rid = str(uuid.uuid4())
    await bind_db[urg.COLLECTION].insert_one(
        {
            "id": rid,
            "order_id": order["id"],
            "po_number": order["po_number"],
            "status": urg.STATUS_APPROVED,
            "reason": "x",
            "target_completion_date": "2026-11-01",
            "notes": "",
            "requested_by_id": "r1",
            "requested_by_email": "r@t.local",
            "requested_by_name": "R",
            "decided_by_id": approver["id"],
            "decided_by_email": approver["email"],
            "decided_by_name": approver["name"],
            "decision_notes": "ok",
            "decision_claim_id": str(uuid.uuid4()),
            "history": [{"action": "approved", "note": "ok"}],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    # Inconsistent: approved but order still normal
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{rid}/decide",
            json={"decision": "approved", "decision_notes": "ok"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["order"]["priority"] == "urgent"
        ord_doc = await bind_db.orders.find_one({"id": order["id"]}, {"_id": 0})
        assert ord_doc["priority"] == "urgent"
        assert urg.order_has_urgent_apply(ord_doc, rid)


@pytest.mark.anyio
async def test_notification_e2e_isolation(bind_db):
    order = await _seed_order(bind_db)
    requester = _user(
        "crm",
        id="req-h",
        email="req@t.local",
        permissions_override={"urgent.request": True},
    )
    notifier = _user(
        "crm",
        id="n1",
        email="n1@t.local",
        permissions_override={"urgent.notify": True, "urgent.approve": True},
    )
    no_perm = _user("crm", id="np", email="np@t.local", permissions_override={})
    inactive = _user(
        "crm",
        id="ina",
        email="ina@t.local",
        permissions_override={"urgent.notify": True},
    )
    director = _user("direktur_utama", id="dir", email="dir@t.local", permissions_override={})
    sa = _user("admin", id="sa", email="sa@t.local", permissions_override={})
    await bind_db.users.insert_many(
        [
            {**requester, "status": "active"},
            {**notifier, "status": "active"},
            {**no_perm, "status": "active"},
            {**inactive, "status": "inactive"},
            {**director, "status": "active", "is_super_admin": False},
            {**sa, "status": "active", "is_super_admin": True},
        ]
    )

    # /auth/me consistency: director/SA without grant
    assert "urgent.notify" not in await user_permissions(director)
    assert "urgent.approve" not in await user_permissions(director)
    assert "urgent.notify" not in await user_permissions(sa)

    recipients = await urg.resolve_notify_recipient_ids(bind_db)
    assert set(recipients) == {notifier["id"]}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        # emit on submit already happened via HTTP route
        notifs = await bind_db[NOTIF_COLLECTION].find({}, {"_id": 0}).to_list(50)
        by_user = {}
        for n in notifs:
            by_user.setdefault(n["user_id"], []).append(n)
        assert notifier["id"] in by_user
        assert requester["id"] not in by_user  # skip self
        assert no_perm["id"] not in by_user
        assert inactive["id"] not in by_user
        assert director["id"] not in by_user
        assert sa["id"] not in by_user
        n0 = by_user[notifier["id"]][0]
        assert n0["event_type"] == urg.EVENT_REQUESTED
        assert n0["deep_link"] == f"/po/{order['id']}"
        assert n0["approval_id"] == req["id"]

        # Retry emit → no duplicate
        await urg.emit_request_notifications(bind_db, order=order, request=req)
        count_after = await bind_db[NOTIF_COLLECTION].count_documents(
            {"approval_id": req["id"], "event_type": urg.EVENT_REQUESTED}
        )
        assert count_after == 1

        # Approve → requester notified
        app.dependency_overrides[current_user] = lambda: notifier
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "go"},
        )
        assert r.status_code == 200
        dec = await bind_db[NOTIF_COLLECTION].find_one(
            {"user_id": requester["id"], "event_type": urg.EVENT_APPROVED},
            {"_id": 0},
        )
        assert dec is not None
        assert "go" in (dec.get("body") or "")
        assert dec["deep_link"] == f"/po/{order['id']}"

        # Retry decision notify → still one
        await urg.emit_decision_notification(
            bind_db, order=r.json()["order"], request=r.json()["request"]
        )
        assert (
            await bind_db[NOTIF_COLLECTION].count_documents(
                {"user_id": requester["id"], "event_type": urg.EVENT_APPROVED}
            )
            == 1
        )


@pytest.mark.anyio
async def test_notification_flush_recovers_failed_delivery(bind_db, monkeypatch):
    """Failed emit leaves durable flag false; flush retries idempotently via dedupe_key."""
    order = await _seed_order(bind_db)
    requester = _user("crm", id="req-flush", email="rf@t.local", permissions_override={"urgent.request": True})
    notifier = _user(
        "crm",
        id="n-flush",
        email="nf@t.local",
        permissions_override={"urgent.notify": True, "urgent.approve": True},
    )
    await bind_db.users.insert_many(
        [{**requester, "status": "active"}, {**notifier, "status": "active"}]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        from services import notifications as notif_mod

        real_create = notif_mod.create_notification
        monkeypatch.setattr(
            notif_mod,
            "create_notification",
            AsyncMock(side_effect=RuntimeError("transient delivery fail")),
        )
        req = await _submit(client, order["id"], requester)
        stored = await bind_db[urg.COLLECTION].find_one({"id": req["id"]}, {"_id": 0})
        assert stored.get(urg.NOTIFY_REQUEST_DELIVERED) is False
        assert await bind_db[NOTIF_COLLECTION].count_documents({"approval_id": req["id"]}) == 0

        # Restore delivery; GET latest triggers flush → recovers without duplicate
        monkeypatch.setattr(notif_mod, "create_notification", real_create)
        app.dependency_overrides[current_user] = lambda: notifier
        r = await client.get(f"/api/orders/{order['id']}/urgent-requests/latest")
        assert r.status_code == 200, r.text
        stored2 = await bind_db[urg.COLLECTION].find_one({"id": req["id"]}, {"_id": 0})
        assert stored2.get(urg.NOTIFY_REQUEST_DELIVERED) is True
        assert (
            await bind_db[NOTIF_COLLECTION].count_documents(
                {"approval_id": req["id"], "event_type": urg.EVENT_REQUESTED}
            )
            == 1
        )

        # Second flush / retry stays idempotent
        await urg.flush_urgent_notifications(bind_db, request=stored2, order=order)
        assert (
            await bind_db[NOTIF_COLLECTION].count_documents(
                {"approval_id": req["id"], "event_type": urg.EVENT_REQUESTED}
            )
            == 1
        )


@pytest.mark.anyio
async def test_notification_failure_does_not_block_decision(bind_db, monkeypatch):
    order = await _seed_order(bind_db)
    requester = _user("crm", id="r2", permissions_override={"urgent.request": True})
    approver = _user("crm", email="a@t.local", permissions_override={"urgent.approve": True})
    await bind_db.users.insert_one({**requester, "status": "active"})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)

        from services import notifications as notif_mod

        monkeypatch.setattr(
            notif_mod,
            "create_notification",
            AsyncMock(side_effect=RuntimeError("delivery fail")),
        )

        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "still ok"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["request"]["status"] == "approved"
        assert r.json()["order"]["priority"] == "urgent"


@pytest.mark.anyio
async def test_legacy_and_old_endpoint_still_blocked(bind_db):
    legacy = await _seed_order(bind_db, priority="urgent")
    before = await bind_db.orders.find_one({"id": legacy["id"]}, {"_id": 0})
    user = _user("admin", permissions_override={"urgent.manage": True, "urgent.request": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: user
        r = await client.post(f"/api/orders/{legacy['id']}/urgent", json={"urgent": False})
        assert r.status_code == 410
    after = await bind_db.orders.find_one({"id": legacy["id"]}, {"_id": 0})
    assert after["priority"] == "urgent"
    assert after.get("urgency_approved_by") == before.get("urgency_approved_by")
    assert after.get("timeline") == before.get("timeline")


def test_preview_route_dev_only():
    src = (Path(__file__).resolve().parents[2] / "frontend/src/App.js").read_text(encoding="utf-8")
    assert "urgent-po-approval-preview" in src
    assert 'process.env.NODE_ENV !== "production"' in src
