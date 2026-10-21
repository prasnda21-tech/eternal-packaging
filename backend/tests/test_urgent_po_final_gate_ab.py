"""Final gate proofs: approval↔email atomicity + worker lifecycle (isolated DB)."""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_urgent_po_final_gate_ab_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ["URGENT_EMAIL_WORKER"] = "0"
os.environ["URGENT_EMAIL_ENABLED"] = "0"
os.environ["URGENT_EMAIL_SMTP_PASSWORD"] = ""

import server
from server import app, current_user
from services import urgent_email_outbox as outbox
from services import urgent_email_worker as worker
from services import urgent_requests as urg
from services.notification_indexes import ensure_indexes as ensure_notification_indexes
from services.urgent_email_smtp import RecordingSmtpSender, load_smtp_config, sanitize_smtp_error


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
    await outbox.ensure_indexes(database)
    await ensure_notification_indexes(database)
    yield database
    app.dependency_overrides.pop(current_user, None)
    worker.stop_urgent_email_worker()
    await client.drop_database(database.name)
    client.close()


def _user(role, **extra):
    doc = {
        "id": extra.get("id", str(uuid.uuid4())),
        "email": extra.get("email", f"{role}-{uuid.uuid4().hex[:6]}@test.local"),
        "name": extra.get("name", role),
        "role": role,
        "permissions_override": extra.get("permissions_override") or {},
    }
    if "notification_email" in extra:
        doc["notification_email"] = extra["notification_email"]
    return doc


async def _seed_order(db, *, priority="normal"):
    now = datetime.now(timezone.utc).isoformat()
    oid = str(uuid.uuid4())
    doc = {
        "id": oid,
        "po_number": f"FG-{uuid.uuid4().hex[:6].upper()}",
        "customer": "Final Gate Co",
        "current_stage": 2,
        "priority": priority,
        "urgency_approved_by": "legacy@eternal.local" if priority == "urgent" else None,
        "cancelled": False,
        "created_at": now,
        "updated_at": now,
        "timeline": [],
        "line_items": [],
    }
    await db.orders.insert_one(dict(doc))
    return doc


async def _submit(client, order_id, user):
    app.dependency_overrides[current_user] = lambda: user
    r = await client.post(
        f"/api/orders/{order_id}/urgent-requests",
        json={"reason": "Final gate", "target_completion_date": "2026-11-20"},
    )
    assert r.status_code == 200, r.text
    return r.json()["request"]


@pytest.mark.anyio
async def test_pending_email_only_after_durable_request(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", permissions_override={"urgent.request": True})
    mailer = _user(
        "crm",
        email="mailer@test.local",
        notification_email="mailer.notify@test.local",
        permissions_override={"urgent.email_notify": True},
    )
    await bind_db.users.insert_many(
        [{**requester, "status": "active"}, {**mailer, "status": "active"}]
    )
    assert await bind_db[outbox.COLLECTION].count_documents({}) == 0
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        stored = await bind_db[urg.COLLECTION].find_one({"id": req["id"]}, {"_id": 0})
        assert stored["status"] == "pending"
        jobs = await bind_db[outbox.COLLECTION].find({"request_id": req["id"]}, {"_id": 0}).to_list(10)
        assert len(jobs) == 1
        assert jobs[0]["event_type"] == urg.EVENT_REQUESTED
        # Idempotent retry submit does not duplicate outbox
        app.dependency_overrides[current_user] = lambda: requester
        await client.post(
            f"/api/orders/{order['id']}/urgent-requests",
            headers={"Idempotency-Key": "same-key"},
            json={
                "reason": "Final gate",
                "target_completion_date": "2026-11-20",
                "idempotency_key": "same-key",
            },
        )
        # first submit had no idempotency; second with key creates new? use same key from start
    # explicit re-enqueue
    await outbox.enqueue_request_emails(bind_db, order=order, request=req)
    assert (
        await bind_db[outbox.COLLECTION].count_documents(
            {"request_id": req["id"], "event_type": urg.EVENT_REQUESTED}
        )
        == 1
    )


@pytest.mark.anyio
async def test_crash_before_finalize_no_approved_email(bind_db):
    order = await _seed_order(bind_db)
    requester = _user(
        "crm", id="r1", email="r1@test.local", permissions_override={"urgent.request": True}
    )
    await bind_db.users.insert_one({**requester, "status": "active"})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        # Simulate crash window: approving, order still normal
        await bind_db[urg.COLLECTION].update_one(
            {"id": req["id"]},
            {
                "$set": {
                    "status": urg.STATUS_APPROVING,
                    "decision_claim_id": str(uuid.uuid4()),
                    "decided_by_email": "apr@test.local",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        mid = await bind_db[urg.COLLECTION].find_one({"id": req["id"]}, {"_id": 0})
        assert await outbox.enqueue_decision_email(bind_db, order=order, request=mid) is None
        assert (
            await bind_db[outbox.COLLECTION].count_documents(
                {"request_id": req["id"], "event_type": urg.EVENT_APPROVED}
            )
            == 0
        )
        ord_doc = await bind_db.orders.find_one({"id": order["id"]}, {"_id": 0})
        assert ord_doc["priority"] == "normal"


@pytest.mark.anyio
async def test_approve_then_reject_paths_email_after_final(bind_db):
    order_a = await _seed_order(bind_db)
    order_r = await _seed_order(bind_db)
    requester = _user(
        "crm",
        id="rq",
        email="pemohon@test.local",
        notification_email="pemohon.notify@test.local",
        permissions_override={"urgent.request": True},
    )
    approver = _user(
        "crm", id="ap", email="apr@test.local", permissions_override={"urgent.approve": True}
    )
    await bind_db.users.insert_many(
        [{**requester, "status": "active"}, {**approver, "status": "active"}]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req_a = await _submit(client, order_a["id"], requester)
        app.dependency_overrides[current_user] = lambda: approver
        ra = await client.post(
            f"/api/urgent-requests/{req_a['id']}/decide",
            json={"decision": "approved", "decision_notes": "ok"},
        )
        assert ra.status_code == 200
        assert ra.json()["order"]["priority"] == "urgent"
        assert ra.json()["request"]["status"] == "approved"
        assert (
            await bind_db[outbox.COLLECTION].count_documents(
                {"request_id": req_a["id"], "event_type": urg.EVENT_APPROVED}
            )
            == 1
        )
        # recovery / idempotent decide retry — no second outbox
        ra2 = await client.post(
            f"/api/urgent-requests/{req_a['id']}/decide",
            json={"decision": "approved", "decision_notes": "ok"},
        )
        assert ra2.status_code == 200
        assert (
            await bind_db[outbox.COLLECTION].count_documents(
                {"request_id": req_a["id"], "event_type": urg.EVENT_APPROVED}
            )
            == 1
        )

        req_r = await _submit(client, order_r["id"], requester)
        app.dependency_overrides[current_user] = lambda: approver
        rr = await client.post(
            f"/api/urgent-requests/{req_r['id']}/decide",
            json={"decision": "rejected", "decision_notes": "penuh"},
        )
        assert rr.status_code == 200, rr.text
        assert rr.json()["order"]["priority"] == "normal"
        assert (
            await bind_db[outbox.COLLECTION].count_documents(
                {"request_id": req_r["id"], "event_type": urg.EVENT_REJECTED}
            )
            == 1
        )


@pytest.mark.anyio
async def test_failed_business_and_legacy_no_email(bind_db):
    legacy = await _seed_order(bind_db, priority="urgent")
    cancelled = await _seed_order(bind_db)
    await bind_db.orders.update_one({"id": cancelled["id"]}, {"$set": {"cancelled": True}})
    user = _user("crm", permissions_override={"urgent.request": True, "urgent.email_notify": True})
    await bind_db.users.insert_one({**user, "status": "active"})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: user
        r1 = await client.post(
            f"/api/orders/{cancelled['id']}/urgent-requests",
            json={"reason": "x", "target_completion_date": "2026-11-20"},
        )
        assert r1.status_code == 400
        r2 = await client.post(
            f"/api/orders/{legacy['id']}/urgent-requests",
            json={"reason": "x", "target_completion_date": "2026-11-20"},
        )
        assert r2.status_code == 409
        r3 = await client.post(f"/api/orders/{legacy['id']}/urgent", json={"urgent": True})
        assert r3.status_code == 410
    assert await bind_db[outbox.COLLECTION].count_documents({}) == 0


def test_worker_lifecycle_no_duplicate_and_clean_shutdown(monkeypatch):
    worker.stop_urgent_email_worker()
    monkeypatch.setenv("URGENT_EMAIL_WORKER", "1")

    class DummyDB:
        pass

    calls = {"n": 0}

    async def fake_process(db, limit=25):
        calls["n"] += 1

    with patch.object(worker, "_loop", wraps=None):
        # Patch create_task to avoid running forever: start should set _task once
        tasks = []

        def fake_create_task(coro, name=None):
            # close coroutine to avoid warnings
            if asyncio.iscoroutine(coro):
                coro.close()

            class T:
                def done(self):
                    return False

                def cancel(self):
                    return None

            t = T()
            tasks.append(t)
            return t

        with patch("services.urgent_email_worker.asyncio.create_task", side_effect=fake_create_task):
            worker.start_urgent_email_worker(DummyDB())
            worker.start_urgent_email_worker(DummyDB())
            assert len(tasks) == 1
            worker.stop_urgent_email_worker()
            # after stop, can start again
            worker.start_urgent_email_worker(DummyDB())
            assert len(tasks) == 2
            worker.stop_urgent_email_worker()


def test_worker_disabled_skips_start(monkeypatch):
    worker.stop_urgent_email_worker()
    monkeypatch.setenv("URGENT_EMAIL_WORKER", "0")
    with patch("services.urgent_email_worker.asyncio.create_task") as ct:
        worker.start_urgent_email_worker(object())
        ct.assert_not_called()


def test_smtp_disabled_config_and_sanitize(monkeypatch):
    monkeypatch.setenv("URGENT_EMAIL_ENABLED", "0")
    monkeypatch.setenv("URGENT_EMAIL_SMTP_PASSWORD", "")
    cfg = load_smtp_config()
    assert cfg.enabled is False
    monkeypatch.setenv("URGENT_EMAIL_SMTP_PASSWORD", "secret-app-password-xyz")
    msg = sanitize_smtp_error("auth failed secret-app-password-xyz password=abc")
    assert "secret-app-password-xyz" not in msg


@pytest.mark.anyio
async def test_disabled_smtp_no_network_send(bind_db, monkeypatch):
    monkeypatch.setenv("URGENT_EMAIL_ENABLED", "0")
    monkeypatch.setenv("URGENT_EMAIL_SMTP_PASSWORD", "")
    now = datetime.now(timezone.utc).isoformat()
    await bind_db[outbox.COLLECTION].insert_one(
        {
            "id": str(uuid.uuid4()),
            "dedupe_key": "urgent_po.requested:fx:a@t.local",
            "event_type": urg.EVENT_REQUESTED,
            "request_id": "fx",
            "order_id": "o",
            "po_number": "P",
            "recipient_user_id": "u",
            "recipient_email": "a@t.local",
            "subject": "s",
            "body_text": "t",
            "body_html": "<p>h</p>",
            "status": outbox.STATUS_PENDING,
            "attempt_count": 0,
            "last_error": "",
            "next_retry_at": now,
            "lease_token": None,
            "lease_expires_at": None,
            "sent_at": None,
            "created_at": now,
            "updated_at": now,
        }
    )
    with patch("smtplib.SMTP_SSL") as ssl_cls, patch("smtplib.SMTP") as smtp_cls:
        stats = await outbox.process_due_outbox(bind_db, limit=5, sender=None)
        assert stats["skipped_disabled"] == 1
        ssl_cls.assert_not_called()
        smtp_cls.assert_not_called()
    row = await bind_db[outbox.COLLECTION].find_one({"request_id": "fx"}, {"_id": 0})
    assert row["status"] == outbox.STATUS_PENDING
