"""Durable Urgent PO email outbox + CAS claim worker helpers.

Enqueue happens only after durable request/decision writes.
SMTP send never rolls back business decisions.
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from permissions import opt_in_permission_granted
from services.urgent_email_smtp import (
    SmtpSender,
    StdlibSmtpSender,
    load_smtp_config,
    sanitize_smtp_error,
)
from services.urgent_email_templates import build_decision_email, build_request_email
from services.urgent_requests import (
    EVENT_APPROVED,
    EVENT_REJECTED,
    EVENT_REQUESTED,
    PERM_EMAIL_NOTIFY,
    STATUS_APPROVED,
    STATUS_REJECTED,
)

COLLECTION = "urgent_email_outbox"

STATUS_PENDING = "pending"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"  # invalid recipient — terminal, no SMTP

logger = logging.getLogger("urgent_email_outbox")

_EMAIL_SIMPLE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat()


def normalize_email(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _validate_email_strict(email: str) -> Tuple[bool, str]:
    """Return (ok, normalized_or_reason_code). Never log full addresses on failure.

    Uses format checks only (no DNS/deliverability). Internal ERP accounts may use
    non-public domains; reject only clearly malformed addresses.
    """
    raw = normalize_email(email)
    if not raw:
        return False, "empty"
    if len(raw) > 254:
        return False, "too_long"
    if not _EMAIL_SIMPLE.match(raw):
        return False, "invalid_format"
    local, _, domain = raw.partition("@")
    if not local or not domain or "." not in domain:
        return False, "invalid_format"
    if ".." in raw or raw.startswith(".") or raw.endswith(".") or "@." in raw:
        return False, "invalid_format"
    return True, raw


def dedupe_key(event_type: str, request_id: str, email: str) -> str:
    return f"{event_type}:{request_id}:{normalize_email(email)}"


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("dedupe_key", unique=True)
    await col.create_index([("status", 1), ("next_retry_at", 1)])
    await col.create_index([("request_id", 1), ("created_at", -1)])
    await col.create_index("lease_expires_at", sparse=True)


def _max_attempts() -> int:
    return max(1, int(os.environ.get("URGENT_EMAIL_MAX_ATTEMPTS") or "8"))


def _lease_seconds() -> int:
    return max(15, int(os.environ.get("URGENT_EMAIL_LEASE_SEC") or "60"))


def _backoff_seconds(attempt: int) -> int:
    # 30s, 60s, 120s, ... capped at 1h
    return min(3600, 30 * (2 ** max(0, attempt - 1)))


async def resolve_email_notify_recipients(db) -> List[dict]:
    """Active users with urgent.email_notify + valid notification_email. No login-email fallback."""
    users = await db.users.find(
        {"status": {"$ne": "inactive"}},
        {
            "_id": 0,
            "id": 1,
            "notification_email": 1,
            "role": 1,
            "permissions_override": 1,
            "status": 1,
            "name": 1,
        },
    ).to_list(2000)
    role_docs = await db.role_permissions.find({}, {"_id": 0, "role": 1, "permissions": 1}).to_list(200)
    role_override = {d["role"]: list(d.get("permissions") or []) for d in role_docs if d.get("role")}

    out: List[dict] = []
    seen_emails = set()
    for u in users:
        if str(u.get("status") or "").strip().lower() == "inactive":
            continue
        override = u.get("permissions_override") or {}
        if not opt_in_permission_granted(u.get("role"), override, PERM_EMAIL_NOTIFY, role_override or None):
            continue
        ok, norm = _validate_email_strict(u.get("notification_email") or "")
        if not ok:
            logger.info(
                "urgent_email skip recipient missing_or_invalid_notification_email user_id=%s reason=%s",
                u.get("id"),
                norm,
            )
            continue
        if norm in seen_emails:
            continue
        seen_emails.add(norm)
        out.append(
            {
                "user_id": str(u.get("id") or ""),
                "email": norm,
                "name": str(u.get("name") or ""),
            }
        )
    return out


async def _insert_outbox(db, doc: dict) -> Optional[dict]:
    try:
        await db[COLLECTION].insert_one(dict(doc))
        doc.pop("_id", None)
        return doc
    except DuplicateKeyError:
        existing = await db[COLLECTION].find_one({"dedupe_key": doc["dedupe_key"]}, {"_id": 0})
        return existing


async def enqueue_request_emails(db, *, order: dict, request: dict) -> List[dict]:
    """Create outbox rows for a durable pending request. Idempotent via dedupe_key."""
    cfg = load_smtp_config()
    content = build_request_email(order=order, request=request, base_url=cfg.app_base_url)
    recipients = await resolve_email_notify_recipients(db)
    requester_id = str(request.get("requested_by_id") or "")
    created = []
    now = now_iso()
    for r in recipients:
        if requester_id and r["user_id"] == requester_id:
            continue
        key = dedupe_key(EVENT_REQUESTED, request["id"], r["email"])
        doc = {
            "id": str(uuid.uuid4()),
            "dedupe_key": key,
            "event_type": EVENT_REQUESTED,
            "request_id": request["id"],
            "order_id": str(order.get("id") or request.get("order_id") or ""),
            "po_number": order.get("po_number") or request.get("po_number"),
            "recipient_user_id": r["user_id"],
            "recipient_email": r["email"],
            "subject": content["subject"],
            "body_text": content["text"],
            "body_html": content["html"],
            "status": STATUS_PENDING,
            "attempt_count": 0,
            "last_error": "",
            "next_retry_at": now,
            "lease_token": None,
            "lease_expires_at": None,
            "sent_at": None,
            "created_at": now,
            "updated_at": now,
        }
        inserted = await _insert_outbox(db, doc)
        if inserted:
            created.append(inserted)
    return created


async def enqueue_decision_email(db, *, order: dict, request: dict) -> Optional[dict]:
    """Outbox for requester decision email. Idempotent."""
    status = str(request.get("status") or "").lower()
    if status == STATUS_APPROVED:
        event = EVENT_APPROVED
    elif status == STATUS_REJECTED:
        event = EVENT_REJECTED
    else:
        return None

    uid = str(request.get("requested_by_id") or "").strip()
    email_raw = str(request.get("requested_by_email") or "").strip()
    user = None
    _user_proj = {"_id": 0, "id": 1, "notification_email": 1, "status": 1}
    if uid:
        user = await db.users.find_one({"id": uid}, _user_proj)
    if not user and email_raw:
        # Lookup by login email identity only — recipient address is notification_email.
        user = await db.users.find_one(
            {"email": {"$regex": f"^{re.escape(email_raw)}$", "$options": "i"}},
            _user_proj,
        )
    if not user or str(user.get("status") or "").lower() == "inactive":
        logger.info("urgent_email decision skip inactive_or_missing request=%s", request.get("id"))
        return None

    ok, norm = _validate_email_strict(user.get("notification_email") or "")
    if not ok:
        now = now_iso()
        # Terminal skipped row so we don't retry forever; unique per request+event
        key = dedupe_key(event, request["id"], f"invalid:{user.get('id') or 'unknown'}")
        doc = {
            "id": str(uuid.uuid4()),
            "dedupe_key": key,
            "event_type": event,
            "request_id": request["id"],
            "order_id": str(order.get("id") or request.get("order_id") or ""),
            "po_number": order.get("po_number") or request.get("po_number"),
            "recipient_user_id": str(user.get("id") or ""),
            "recipient_email": "",
            "subject": "",
            "body_text": "",
            "body_html": "",
            "status": STATUS_SKIPPED,
            "attempt_count": 0,
            "last_error": f"invalid_notification_email:{norm}",
            "next_retry_at": now,
            "lease_token": None,
            "lease_expires_at": None,
            "sent_at": None,
            "created_at": now,
            "updated_at": now,
        }
        return await _insert_outbox(db, doc)

    cfg = load_smtp_config()
    content = build_decision_email(order=order, request=request, base_url=cfg.app_base_url)
    now = now_iso()
    key = dedupe_key(event, request["id"], norm)
    doc = {
        "id": str(uuid.uuid4()),
        "dedupe_key": key,
        "event_type": event,
        "request_id": request["id"],
        "order_id": str(order.get("id") or request.get("order_id") or ""),
        "po_number": order.get("po_number") or request.get("po_number"),
        "recipient_user_id": str(user.get("id") or ""),
        "recipient_email": norm,
        "subject": content["subject"],
        "body_text": content["text"],
        "body_html": content["html"],
        "status": STATUS_PENDING,
        "attempt_count": 0,
        "last_error": "",
        "next_retry_at": now,
        "lease_token": None,
        "lease_expires_at": None,
        "sent_at": None,
        "created_at": now,
        "updated_at": now,
    }
    return await _insert_outbox(db, doc)


async def claim_next_job(db, *, now: Optional[datetime] = None) -> Optional[dict]:
    """CAS claim one due outbox job. Recovers expired leases."""
    ref = now or now_utc()
    ref_iso = ref.isoformat()
    lease_token = str(uuid.uuid4())
    lease_exp = (ref + timedelta(seconds=_lease_seconds())).isoformat()
    query = {
        "$or": [
            {
                "status": {"$in": [STATUS_PENDING, STATUS_FAILED]},
                "next_retry_at": {"$lte": ref_iso},
            },
            {
                "status": STATUS_SENDING,
                "lease_expires_at": {"$lte": ref_iso},
            },
        ]
    }
    claimed = await db[COLLECTION].find_one_and_update(
        query,
        {
            "$set": {
                "status": STATUS_SENDING,
                "lease_token": lease_token,
                "lease_expires_at": lease_exp,
                "updated_at": ref_iso,
            }
        },
        sort=[("next_retry_at", 1), ("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )
    if claimed:
        claimed.pop("_id", None)
    return claimed


async def mark_sent(db, *, job: dict, lease_token: str) -> Optional[dict]:
    now = now_iso()
    return await db[COLLECTION].find_one_and_update(
        {"id": job["id"], "lease_token": lease_token, "status": STATUS_SENDING},
        {
            "$set": {
                "status": STATUS_SENT,
                "sent_at": now,
                "updated_at": now,
                "lease_token": None,
                "lease_expires_at": None,
                "last_error": "",
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def mark_failed(db, *, job: dict, lease_token: str, error: str) -> Optional[dict]:
    now_dt = now_utc()
    attempts = int(job.get("attempt_count") or 0) + 1
    max_a = _max_attempts()
    safe_err = sanitize_smtp_error(error)
    if attempts >= max_a:
        status = STATUS_FAILED
        next_retry = (now_dt + timedelta(days=3650)).isoformat()  # park
    else:
        status = STATUS_FAILED
        next_retry = (now_dt + timedelta(seconds=_backoff_seconds(attempts))).isoformat()
    return await db[COLLECTION].find_one_and_update(
        {"id": job["id"], "lease_token": lease_token, "status": STATUS_SENDING},
        {
            "$set": {
                "status": status,
                "attempt_count": attempts,
                "last_error": safe_err,
                "next_retry_at": next_retry,
                "updated_at": now_dt.isoformat(),
                "lease_token": None,
                "lease_expires_at": None,
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def process_one_job(
    db,
    job: dict,
    *,
    sender: Optional[SmtpSender] = None,
) -> str:
    """Send one claimed job. Returns outcome: sent|failed|skipped_disabled|noop."""
    token = str(job.get("lease_token") or "")
    if job.get("status") == STATUS_SKIPPED:
        return "skipped"
    cfg = load_smtp_config()
    if sender is None:
        if not cfg.enabled:
            # Release claim without burning attempts hard — short retry until configured
            now_dt = now_utc()
            await db[COLLECTION].update_one(
                {"id": job["id"], "lease_token": token, "status": STATUS_SENDING},
                {
                    "$set": {
                        "status": STATUS_PENDING,
                        "last_error": "smtp_disabled_or_unconfigured",
                        "next_retry_at": (now_dt + timedelta(minutes=5)).isoformat(),
                        "updated_at": now_dt.isoformat(),
                        "lease_token": None,
                        "lease_expires_at": None,
                    }
                },
            )
            return "skipped_disabled"
        sender = StdlibSmtpSender(cfg)

    to_addr = normalize_email(job.get("recipient_email"))
    if not to_addr:
        await mark_failed(db, job=job, lease_token=token, error="empty_recipient")
        return "failed"

    try:
        sender.send(
            to_addr=to_addr,
            subject=str(job.get("subject") or ""),
            text=str(job.get("body_text") or ""),
            html=str(job.get("body_html") or ""),
        )
        await mark_sent(db, job=job, lease_token=token)
        return "sent"
    except Exception as exc:
        logger.warning(
            "urgent_email send failed job=%s event=%s err=%s",
            job.get("id"),
            job.get("event_type"),
            sanitize_smtp_error(exc),
        )
        await mark_failed(db, job=job, lease_token=token, error=str(exc))
        return "failed"


async def process_due_outbox(
    db,
    *,
    limit: int = 20,
    sender: Optional[SmtpSender] = None,
) -> Dict[str, Any]:
    stats = {"claimed": 0, "sent": 0, "failed": 0, "skipped_disabled": 0, "skipped": 0}
    for _ in range(max(1, min(int(limit or 20), 100))):
        job = await claim_next_job(db)
        if not job:
            break
        stats["claimed"] += 1
        outcome = await process_one_job(db, job, sender=sender)
        stats[outcome] = stats.get(outcome, 0) + 1
    return stats
