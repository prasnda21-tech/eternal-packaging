"""Urgent PO Phase B — Gmail email outbox (isolated DB, mock SMTP, no real outbound)."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("DB_NAME", "eternal_urgent_po_phase_b_email_test")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")
os.environ.setdefault("STORAGE_PROVIDER", "local")
os.environ["URGENT_EMAIL_WORKER"] = "0"
os.environ["URGENT_EMAIL_ENABLED"] = "0"
os.environ["URGENT_EMAIL_SMTP_PASSWORD"] = ""
os.environ["URGENT_EMAIL_APP_BASE_URL"] = "https://produksi.eternalpackaging.id"

import server
from permissions import DIRECTOR_MUST_OPT_IN_PERMISSIONS, director_full_permission_set
from server import app, current_user, user_permissions
from services import urgent_email_outbox as outbox
from services import urgent_requests as urg
from services.notification_indexes import ensure_indexes as ensure_notification_indexes
from services.urgent_email_smtp import RecordingSmtpSender, sanitize_smtp_error
from services.urgent_email_templates import build_decision_email, build_request_email, po_deep_link


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
        "po_number": f"PO-B-{uuid.uuid4().hex[:6].upper()}",
        "customer": "Customer Phase B",
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
        json={"reason": "Butuh cepat kirim", "target_completion_date": "2026-11-10", "notes": "n"},
    )
    assert r.status_code == 200, r.text
    return r.json()["request"]


def test_email_notify_opt_in_director_sa():
    assert "urgent.email_notify" in DIRECTOR_MUST_OPT_IN_PERMISSIONS
    assert "urgent.email_notify" not in director_full_permission_set()


def test_templates_and_deep_link():
    order = {"id": "ord-1", "po_number": "PO-1", "customer": "PT Contoh"}
    req = {
        "id": "req-1",
        "reason": "Alasan",
        "target_completion_date": "2026-11-01",
        "requested_by_name": "Budi",
        "status": "pending",
    }
    built = build_request_email(
        order=order, request=req, base_url="https://produksi.eternalpackaging.id"
    )
    assert "PO-1" in built["subject"]
    assert "Menunggu Persetujuan" in built["text"]
    assert "https://produksi.eternalpackaging.id/po/ord-1" in built["text"]
    assert "Eternal Packaging" in built["html"] or "ETERNAL PACKAGING" in built["html"]
    assert "#1B7A4E" in built["html"]
    assert "#D97706" in built["html"]
    assert "<!DOCTYPE html>" in built["html"]

    req_a = {**req, "status": "approved", "decided_by_name": "Ani", "decision_notes": "OK"}
    dec = build_decision_email(
        order=order, request=req_a, base_url="https://produksi.eternalpackaging.id"
    )
    assert "Disetujui" in dec["subject"]
    assert "Ani" in dec["text"]
    assert po_deep_link("https://produksi.eternalpackaging.id", "ord-1").endswith("/po/ord-1")

    req_r = {**req, "status": "rejected", "decided_by_name": "Ani", "decision_notes": "Penuh"}
    rej = build_decision_email(
        order=order, request=req_r, base_url="https://produksi.eternalpackaging.id"
    )
    assert "Ditolak" in rej["subject"]
    assert "Penuh" in rej["text"]


def test_sanitize_hides_secret(monkeypatch):
    monkeypatch.setenv("URGENT_EMAIL_SMTP_PASSWORD", "super-secret-app-pass")
    msg = sanitize_smtp_error("login failed password=super-secret-app-pass detail")
    assert "super-secret-app-pass" not in msg
    assert "REDACTED" in msg or "password" in msg.lower()


@pytest.mark.anyio
async def test_recipients_permission_inactive_director_sa_invalid_dedupe(bind_db):
    order = await _seed_order(bind_db)
    requester = _user("crm", id="req1", email="req@test.local", permissions_override={"urgent.request": True})
    on = _user(
        "crm",
        id="on1",
        email="login.on@test.local",  # login ≠ notification
        notification_email="On.User@Test.Local",
        permissions_override={"urgent.email_notify": True},
    )
    on_dup = _user(
        "crm",
        id="on2",
        email="login.on2@test.local",
        notification_email="on.user@test.local",  # same normalized notification
        permissions_override={"urgent.email_notify": True},
    )
    # Permission ON but empty notification_email → not a recipient (no login fallback)
    empty_notify = _user(
        "crm",
        id="empty1",
        email="has-login@test.local",
        notification_email="",
        permissions_override={"urgent.email_notify": True},
    )
    # Old user without notification_email field → skip
    legacy_no_field = _user(
        "crm",
        id="leg1",
        email="legacy-login@test.local",
        permissions_override={"urgent.email_notify": True},
    )
    off = _user(
        "crm",
        id="off1",
        email="off@test.local",
        notification_email="off-notify@test.local",
        permissions_override={},
    )
    inactive = _user(
        "crm",
        id="ina1",
        email="ina@test.local",
        notification_email="ina-notify@test.local",
        permissions_override={"urgent.email_notify": True},
    )
    bad = _user(
        "crm",
        id="bad1",
        email="bad-login@test.local",
        notification_email="not-an-email",
        permissions_override={"urgent.email_notify": True},
    )
    director = _user(
        "direktur_utama",
        id="dir1",
        email="dir@test.local",
        notification_email="dir-notify@test.local",
        permissions_override={},
    )
    sa = _user(
        "admin",
        id="sa1",
        email="sa@test.local",
        notification_email="sa-notify@test.local",
        permissions_override={},
    )
    await bind_db.users.insert_many(
        [
            {**requester, "status": "active"},
            {**on, "status": "active"},
            {**on_dup, "status": "active"},
            {**empty_notify, "status": "active"},
            {**legacy_no_field, "status": "active"},
            {**off, "status": "active"},
            {**inactive, "status": "inactive"},
            {**bad, "status": "active"},
            {**director, "status": "active"},
            {**sa, "status": "active", "is_super_admin": True},
        ]
    )
    assert "urgent.email_notify" not in await user_permissions(director)
    assert "urgent.email_notify" not in await user_permissions(sa)

    recips = await outbox.resolve_email_notify_recipients(bind_db)
    emails = [r["email"] for r in recips]
    assert emails == ["on.user@test.local"]
    assert "login.on@test.local" not in emails
    assert "has-login@test.local" not in emails

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        rows = await bind_db[outbox.COLLECTION].find({"request_id": req["id"]}, {"_id": 0}).to_list(20)
        assert len(rows) == 1
        assert rows[0]["recipient_email"] == "on.user@test.local"
        assert rows[0]["event_type"] == urg.EVENT_REQUESTED
        assert rows[0]["status"] == outbox.STATUS_PENDING
        assert "produksi.eternalpackaging.id/po/" in rows[0]["body_text"]


@pytest.mark.anyio
async def test_decision_email_and_smtp_failure_no_rollback(bind_db):
    order = await _seed_order(bind_db)
    requester = _user(
        "crm",
        id="r-dec",
        email="pemohon@test.local",
        notification_email="pemohon.notify@gmail.com",
        permissions_override={"urgent.request": True},
    )
    approver = _user(
        "crm", id="a-dec", email="apr@test.local", permissions_override={"urgent.approve": True}
    )
    await bind_db.users.insert_many(
        [{**requester, "status": "active"}, {**approver, "status": "active"}]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "OK kirim"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["order"]["priority"] == "urgent"
        assert r.json()["request"]["status"] == "approved"

        jobs = await bind_db[outbox.COLLECTION].find(
            {"request_id": req["id"], "event_type": urg.EVENT_APPROVED}, {"_id": 0}
        ).to_list(10)
        assert len(jobs) == 1
        assert jobs[0]["recipient_email"] == "pemohon.notify@gmail.com"
        assert jobs[0]["recipient_email"] != requester["email"]

        sender = RecordingSmtpSender(fail_forever=True)
        await outbox.process_due_outbox(bind_db, limit=10, sender=sender)
        # approval still urgent despite SMTP failure
        ord_doc = await bind_db.orders.find_one({"id": order["id"]}, {"_id": 0})
        assert ord_doc["priority"] == "urgent"
        failed = await bind_db[outbox.COLLECTION].count_documents(
            {"request_id": req["id"], "status": outbox.STATUS_FAILED}
        )
        assert failed >= 1
        assert sender.attempts >= 1


@pytest.mark.anyio
async def test_reject_email_retry_idempotent_and_send_ok(bind_db):
    order = await _seed_order(bind_db)
    requester = _user(
        "crm",
        id="r-rej",
        email="pemohon2@test.local",
        notification_email="pemohon2.notify@yahoo.com",
        permissions_override={"urgent.request": True},
    )
    approver = _user(
        "crm", id="a-rej", email="apr2@test.local", permissions_override={"urgent.approve": True}
    )
    await bind_db.users.insert_many(
        [{**requester, "status": "active"}, {**approver, "status": "active"}]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        # duplicate enqueue
        await outbox.enqueue_request_emails(bind_db, order=order, request=req)
        assert await bind_db[outbox.COLLECTION].count_documents(
            {"request_id": req["id"], "event_type": urg.EVENT_REQUESTED}
        ) == 0  # no email_notify recipients in this test

        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "rejected", "decision_notes": "Kapasitas penuh"},
        )
        assert r.status_code == 200
        assert r.json()["order"]["priority"] == "normal"
        # retry decide enqueue
        await outbox.enqueue_decision_email(bind_db, order=order, request=r.json()["request"])
        assert (
            await bind_db[outbox.COLLECTION].count_documents(
                {"request_id": req["id"], "event_type": urg.EVENT_REJECTED}
            )
            == 1
        )

        sender = RecordingSmtpSender()
        stats = await outbox.process_due_outbox(bind_db, limit=5, sender=sender)
        assert stats["sent"] == 1
        assert len(sender.sent) == 1
        assert sender.sent[0]["to"] == "pemohon2.notify@yahoo.com"
        assert "Kapasitas penuh" in sender.sent[0]["text"]
        assert "produksi.eternalpackaging.id/po/" in sender.sent[0]["text"]


@pytest.mark.anyio
async def test_decision_empty_notification_email_skipped_no_login_fallback(bind_db):
    order = await _seed_order(bind_db)
    requester = _user(
        "crm",
        id="r-empty",
        email="has-login-only@test.local",
        notification_email="",
        permissions_override={"urgent.request": True},
    )
    approver = _user(
        "crm", id="a-empty", email="apr-empty@test.local", permissions_override={"urgent.approve": True}
    )
    await bind_db.users.insert_many(
        [{**requester, "status": "active"}, {**approver, "status": "active"}]
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        req = await _submit(client, order["id"], requester)
        app.dependency_overrides[current_user] = lambda: approver
        r = await client.post(
            f"/api/urgent-requests/{req['id']}/decide",
            json={"decision": "approved", "decision_notes": "ok"},
        )
        assert r.status_code == 200
        jobs = await bind_db[outbox.COLLECTION].find(
            {"request_id": req["id"], "event_type": urg.EVENT_APPROVED}, {"_id": 0}
        ).to_list(10)
        assert len(jobs) == 1
        assert jobs[0]["status"] == outbox.STATUS_SKIPPED
        assert jobs[0]["recipient_email"] == ""
        assert "has-login-only@test.local" not in (jobs[0].get("recipient_email") or "")


@pytest.mark.anyio
async def test_outbox_snapshot_not_rewritten_when_user_notification_changes(bind_db):
    """Pending outbox keeps recipient_email snapshot; user edit must not mutate it."""
    now = datetime.now(timezone.utc).isoformat()
    jid = str(uuid.uuid4())
    await bind_db[outbox.COLLECTION].insert_one(
        {
            "id": jid,
            "dedupe_key": "urgent_po.requested:snap:old@notify.local",
            "event_type": urg.EVENT_REQUESTED,
            "request_id": "snap-req",
            "order_id": "o1",
            "po_number": "P1",
            "recipient_user_id": "u-snap",
            "recipient_email": "old@notify.local",
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
    await bind_db.users.insert_one(
        {
            "id": "u-snap",
            "email": "login@test.local",
            "notification_email": "new@notify.local",
            "status": "active",
            "role": "crm",
            "permissions_override": {"urgent.email_notify": True},
        }
    )
    row = await bind_db[outbox.COLLECTION].find_one({"id": jid}, {"_id": 0})
    assert row["recipient_email"] == "old@notify.local"
    sender = RecordingSmtpSender()
    job = await outbox.claim_next_job(bind_db)
    assert await outbox.process_one_job(bind_db, job, sender=sender) == "sent"
    assert sender.sent[0]["to"] == "old@notify.local"


@pytest.mark.anyio
async def test_concurrent_claim_and_lease_expiry(bind_db):
    now = datetime.now(timezone.utc)
    for i in range(2):
        await bind_db[outbox.COLLECTION].insert_one(
            {
                "id": str(uuid.uuid4()),
                "dedupe_key": f"urgent_po.requested:reqx:user{i}@t.local",
                "event_type": urg.EVENT_REQUESTED,
                "request_id": "reqx",
                "order_id": "o1",
                "po_number": "P1",
                "recipient_user_id": f"u{i}",
                "recipient_email": f"user{i}@t.local",
                "subject": "s",
                "body_text": "t",
                "body_html": "<p>h</p>",
                "status": outbox.STATUS_PENDING,
                "attempt_count": 0,
                "last_error": "",
                "next_retry_at": now.isoformat(),
                "lease_token": None,
                "lease_expires_at": None,
                "sent_at": None,
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
            }
        )
    a = await outbox.claim_next_job(bind_db)
    b = await outbox.claim_next_job(bind_db)
    assert a and b
    assert a["id"] != b["id"]
    assert a["status"] == outbox.STATUS_SENDING
    c = await outbox.claim_next_job(bind_db)
    assert c is None

    # Expire lease on a → recoverable
    expired = (now - timedelta(seconds=5)).isoformat()
    await bind_db[outbox.COLLECTION].update_one(
        {"id": a["id"]},
        {"$set": {"lease_expires_at": expired, "status": outbox.STATUS_SENDING}},
    )
    recovered = await outbox.claim_next_job(bind_db)
    assert recovered is not None
    assert recovered["id"] == a["id"]

    sender = RecordingSmtpSender()
    assert await outbox.process_one_job(bind_db, recovered, sender=sender) == "sent"
    assert len(sender.sent) == 1


@pytest.mark.anyio
async def test_no_email_for_legacy_urgent(bind_db):
    legacy = await _seed_order(bind_db, priority="urgent")
    before = await bind_db[outbox.COLLECTION].count_documents({})
    # Opening latest must not enqueue email for legacy
    user = _user("crm", permissions_override={"urgent.review": True})
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        app.dependency_overrides[current_user] = lambda: user
        r = await client.get(f"/api/orders/{legacy['id']}/urgent-requests/latest")
        assert r.status_code == 200
        assert r.json()["display_state"] == "LEGACY_URGENT"
    assert await bind_db[outbox.COLLECTION].count_documents({}) == before


@pytest.mark.anyio
async def test_disabled_smtp_does_not_send_and_keeps_pending(bind_db, monkeypatch):
    monkeypatch.setenv("URGENT_EMAIL_ENABLED", "0")
    monkeypatch.setenv("URGENT_EMAIL_SMTP_PASSWORD", "")
    now = datetime.now(timezone.utc).isoformat()
    await bind_db[outbox.COLLECTION].insert_one(
        {
            "id": str(uuid.uuid4()),
            "dedupe_key": "urgent_po.requested:r1:a@t.local",
            "event_type": urg.EVENT_REQUESTED,
            "request_id": "r1",
            "order_id": "o1",
            "po_number": "P1",
            "recipient_user_id": "u1",
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
    stats = await outbox.process_due_outbox(bind_db, limit=5, sender=None)
    assert stats["skipped_disabled"] == 1
    row = await bind_db[outbox.COLLECTION].find_one({"request_id": "r1"}, {"_id": 0})
    assert row["status"] == outbox.STATUS_PENDING
    assert "smtp_disabled" in (row.get("last_error") or "")


@pytest.mark.anyio
async def test_smtp_retry_after_failure(bind_db):
    now = datetime.now(timezone.utc).isoformat()
    jid = str(uuid.uuid4())
    await bind_db[outbox.COLLECTION].insert_one(
        {
            "id": jid,
            "dedupe_key": "urgent_po.approved:r2:b@t.local",
            "event_type": urg.EVENT_APPROVED,
            "request_id": "r2",
            "order_id": "o2",
            "po_number": "P2",
            "recipient_user_id": "u2",
            "recipient_email": "b@t.local",
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
    sender = RecordingSmtpSender(fail_times=1)
    # first attempt fails
    job = await outbox.claim_next_job(bind_db)
    assert await outbox.process_one_job(bind_db, job, sender=sender) == "failed"
    row = await bind_db[outbox.COLLECTION].find_one({"id": jid}, {"_id": 0})
    assert row["status"] == outbox.STATUS_FAILED
    assert row["attempt_count"] == 1
    # make due again
    await bind_db[outbox.COLLECTION].update_one(
        {"id": jid}, {"$set": {"next_retry_at": datetime.now(timezone.utc).isoformat()}}
    )
    job2 = await outbox.claim_next_job(bind_db)
    assert await outbox.process_one_job(bind_db, job2, sender=sender) == "sent"
    assert len(sender.sent) == 1
