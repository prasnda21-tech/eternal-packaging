"""Urgent PO approval requests — Phase A functional workflow.

orders.priority remains the operational urgent flag. This collection stores
approval workflow documents only. Legacy urgent POs (priority=urgent with no
approved request) are never rewritten.
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

COLLECTION = "urgent_requests"
STATUS_PENDING = "pending"
STATUS_APPROVING = "approving"  # durable claim while applying order priority
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"

PERM_REQUEST = "urgent.request"
PERM_REVIEW = "urgent.review"
PERM_APPROVE = "urgent.approve"
PERM_NOTIFY = "urgent.notify"
PERM_EMAIL_NOTIFY = "urgent.email_notify"

EVENT_REQUESTED = "urgent_po.requested"
EVENT_APPROVED = "urgent_po.approved"
EVENT_REJECTED = "urgent_po.rejected"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
REASON_MAX = 500
NOTES_MAX = 500
# Abandoned approving claims older than this may be released / reclaimed.
CLAIM_STALE_SECONDS = 120

logger = logging.getLogger("urgent_requests")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def claim_is_stale(request: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    """True when an approving claim is old enough to auto-release safely."""
    if not request or request.get("status") != STATUS_APPROVING:
        return False
    stamp = _parse_iso(request.get("updated_at") or request.get("decided_at"))
    if stamp is None:
        return True
    ref = now or datetime.now(timezone.utc)
    return (ref - stamp).total_seconds() >= CLAIM_STALE_SECONDS


def order_has_urgent_apply(order: Optional[dict], request_id: str) -> bool:
    rid = str(request_id or "")
    if not order or not rid:
        return False
    for entry in order.get("timeline") or []:
        if isinstance(entry, dict) and str(entry.get("urgent_request_id") or "") == rid:
            return True
    return False


def history_has_action(request: Optional[dict], action: str) -> bool:
    for entry in (request or {}).get("history") or []:
        if isinstance(entry, dict) and entry.get("action") == action:
            return True
    return False


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _uid(user: Optional[dict]) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("id") or user.get("user_id") or "").strip()


def _email(user: Optional[dict]) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("email") or "").strip()


def _name(user: Optional[dict]) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("name") or user.get("email") or "").strip()


def public_request(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    out = {k: v for k, v in doc.items() if k != "_id"}
    return out


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("idempotency_key", unique=True, sparse=True)
    # One active pending/approving request per PO
    await col.create_index(
        [("order_id", 1)],
        unique=True,
        partialFilterExpression={"status": {"$in": [STATUS_PENDING, STATUS_APPROVING]}},
        name="uniq_urgent_active_per_order",
    )
    await col.create_index([("order_id", 1), ("created_at", -1)])
    await col.create_index([("status", 1), ("created_at", -1)])
    await col.create_index([("requested_by_id", 1), ("created_at", -1)])


def classify_display(priority: Optional[str], request: Optional[dict]) -> str:
    is_urgent = str(priority or "").strip().lower() == "urgent"
    status = str((request or {}).get("status") or "").lower()
    if status in (STATUS_PENDING, STATUS_APPROVING):
        return "PENDING_REQUEST"
    if is_urgent and status == STATUS_APPROVED:
        return "APPROVED_URGENT"
    if is_urgent:
        return "LEGACY_URGENT"
    return "NORMAL"


def _validate_submit_fields(reason: str, target_date: str, notes: str) -> Dict[str, str]:
    reason_t = str(reason or "").strip()
    target = str(target_date or "").strip()
    notes_t = str(notes or "").strip()
    if not reason_t:
        raise HTTPException(400, "Alasan urgent wajib diisi.")
    if len(reason_t) > REASON_MAX:
        raise HTTPException(400, f"Alasan maksimal {REASON_MAX} karakter.")
    if not target or not DATE_RE.match(target):
        raise HTTPException(400, "Target selesai wajib diisi (YYYY-MM-DD).")
    if len(notes_t) > NOTES_MAX:
        raise HTTPException(400, f"Catatan maksimal {NOTES_MAX} karakter.")
    return {"reason": reason_t, "target_date": target, "notes": notes_t}


async def get_active_request(db, order_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one(
        {"order_id": str(order_id), "status": {"$in": [STATUS_PENDING, STATUS_APPROVING]}},
        {"_id": 0},
    )


async def get_latest_request_for_order(db, order_id: str) -> Optional[dict]:
    docs = await db[COLLECTION].find(
        {"order_id": str(order_id)},
        {"_id": 0},
    ).sort("created_at", -1).to_list(1)
    return docs[0] if docs else None


async def get_request(db, request_id: str) -> Optional[dict]:
    return await db[COLLECTION].find_one({"id": str(request_id)}, {"_id": 0})


def can_read_request(user: dict, request: dict, *, has_review: bool, has_approve: bool) -> bool:
    if has_review or has_approve:
        return True
    uid = _uid(user)
    email = _email(user).lower()
    if uid and str(request.get("requested_by_id") or "") == uid:
        return True
    if email and str(request.get("requested_by_email") or "").lower() == email:
        return True
    return False


async def submit_request(
    db,
    *,
    order: dict,
    user: dict,
    reason: str,
    target_completion_date: str,
    notes: str = "",
    idempotency_key: Optional[str] = None,
) -> dict:
    if order.get("cancelled"):
        raise HTTPException(400, "PO dibatalkan tidak dapat diajukan Urgent.")
    if str(order.get("priority") or "").lower() == "urgent":
        raise HTTPException(409, "PO sudah berstatus Urgent.")

    fields = _validate_submit_fields(reason, target_completion_date, notes)
    key = str(idempotency_key or "").strip() or None
    if key:
        existing = await db[COLLECTION].find_one({"idempotency_key": key}, {"_id": 0})
        if existing:
            return existing

    active = await get_active_request(db, order["id"])
    if active:
        raise HTTPException(409, "Sudah ada pengajuan Urgent Pending untuk PO ini.")

    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "order_id": order["id"],
        "po_number": order.get("po_number"),
        "status": STATUS_PENDING,
        "reason": fields["reason"],
        "target_completion_date": fields["target_date"],
        "notes": fields["notes"],
        "requested_by_id": _uid(user),
        "requested_by_email": _email(user),
        "requested_by_name": _name(user),
        "requested_at": now,
        "decided_by_id": None,
        "decided_by_email": None,
        "decided_by_name": None,
        "decided_at": None,
        "decision_notes": "",
        "decision_claim_id": None,
        "created_at": now,
        "updated_at": now,
        "history": [
            {
                "action": "submitted",
                "at": now,
                "by_id": _uid(user),
                "by_email": _email(user),
                "by_name": _name(user),
                "note": fields["reason"],
            }
        ],
        "notification_emitted": False,
    }
    # Omit null idempotency_key — sparse unique index treats null as a real value
    # and would block a second request without a key.
    if key:
        doc["idempotency_key"] = key
    try:
        await db[COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError:
        # Concurrent pending or idempotency race
        if key:
            existing = await db[COLLECTION].find_one({"idempotency_key": key}, {"_id": 0})
            if existing:
                return existing
        active = await get_active_request(db, order["id"])
        if active:
            raise HTTPException(409, "Sudah ada pengajuan Urgent Pending untuk PO ini.")
        raise HTTPException(409, "Pengajuan bentrok. Silakan coba lagi.")
    doc.pop("_id", None)
    return doc


async def cancel_request(db, *, request: dict, user: dict) -> dict:
    if request.get("status") != STATUS_PENDING:
        raise HTTPException(409, "Hanya pengajuan Pending yang dapat dibatalkan.")
    uid = _uid(user)
    email = _email(user).lower()
    owner = (
        (uid and str(request.get("requested_by_id") or "") == uid)
        or (email and str(request.get("requested_by_email") or "").lower() == email)
    )
    if not owner:
        raise HTTPException(403, "Hanya pemohon yang dapat membatalkan pengajuan.")

    now = now_iso()
    history_entry = {
        "action": "cancelled",
        "at": now,
        "by_id": uid,
        "by_email": _email(user),
        "by_name": _name(user),
        "note": "",
    }
    updated = await db[COLLECTION].find_one_and_update(
        {"id": request["id"], "status": STATUS_PENDING},
        {
            "$set": {"status": STATUS_CANCELLED, "updated_at": now},
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(409, "Pengajuan sudah diproses.")
    updated.pop("_id", None)
    return updated


async def decide_request(
    db,
    *,
    request: dict,
    order: dict,
    user: dict,
    decision: str,
    decision_notes: str = "",
) -> Dict[str, Any]:
    """Approve or reject with durable claim. Returns {request, order}."""
    d = str(decision or "").strip().lower()
    if d not in ("approved", "rejected"):
        raise HTTPException(400, "Keputusan harus approved atau rejected.")
    notes = str(decision_notes or "").strip()
    if d == "rejected" and not notes:
        raise HTTPException(400, "Alasan penolakan wajib diisi.")
    if len(notes) > NOTES_MAX:
        raise HTTPException(400, f"Catatan maksimal {NOTES_MAX} karakter.")

    if order.get("cancelled"):
        raise HTTPException(400, "PO dibatalkan tidak dapat diputuskan.")

    # Always re-read request for CAS freshness / crash recovery.
    fresh = await get_request(db, request["id"]) or request
    order_doc = await db.orders.find_one({"id": order["id"]}, {"_id": 0}) or order

    # Auto-heal: approved request must have verified urgent priority.
    if fresh.get("status") == STATUS_APPROVED:
        order_doc = await _ensure_order_matches_approved(db, request=fresh, order=order_doc)
        actor_email = _email(user).lower()
        decided_email = str(fresh.get("decided_by_email") or "").lower()
        if d == "approved" and decided_email and decided_email == actor_email:
            return {"request": fresh, "order": order_doc}
        raise HTTPException(409, "Pengajuan sudah diputuskan.")

    if fresh.get("status") == STATUS_REJECTED:
        actor_email = _email(user).lower()
        decided_email = str(fresh.get("decided_by_email") or "").lower()
        if d == "rejected" and decided_email and decided_email == actor_email:
            return {"request": fresh, "order": order_doc}
        raise HTTPException(409, "Pengajuan sudah diputuskan.")

    if fresh.get("status") not in (STATUS_PENDING, STATUS_APPROVING):
        raise HTTPException(409, "Pengajuan sudah diputuskan.")

    if d == "rejected":
        return await _finalize_reject(db, request=fresh, user=user, notes=notes)

    return await _finalize_approve(db, request=fresh, order=order_doc, user=user, notes=notes)


async def _ensure_order_matches_approved(db, *, request: dict, order: dict) -> dict:
    """If request is approved but order is not urgent, re-apply priority idempotently."""
    if not order:
        return order
    if str(order.get("priority") or "").lower() == "urgent" and order_has_urgent_apply(order, request["id"]):
        return order
    now = now_iso()
    actor_email = str(request.get("decided_by_email") or "")
    timeline_entry = {
        "stage": order.get("current_stage") or 0,
        "stage_label": "Urgent Approval",
        "at": now,
        "by_email": actor_email,
        "by_name": request.get("decided_by_name"),
        "note": f"Urgent disetujui via pengajuan {request.get('id')} (reconcile)",
        "urgent_request_id": request["id"],
    }
    update: Dict[str, Any] = {
        "$set": {
            "priority": "urgent",
            "urgency_approved_by": actor_email or order.get("urgency_approved_by"),
            "updated_at": now,
        }
    }
    if not order_has_urgent_apply(order, request["id"]):
        update["$push"] = {"timeline": timeline_entry}
    applied = await db.orders.find_one_and_update(
        {"id": order["id"], "cancelled": {"$ne": True}},
        update,
        return_document=ReturnDocument.AFTER,
    )
    if applied:
        applied.pop("_id", None)
        return applied
    return order


async def _finalize_reject(db, *, request: dict, user: dict, notes: str) -> Dict[str, Any]:
    now = now_iso()
    claim_id = str(uuid.uuid4())
    actor_email = _email(user)
    # If stuck in approving: same actor or stale claim → release, then reject.
    if request.get("status") == STATUS_APPROVING:
        holder = str(request.get("decided_by_email") or "").lower()
        if holder == actor_email.lower() or claim_is_stale(request):
            await _release_claim(db, request["id"], request.get("decision_claim_id") or "")
        else:
            raise HTTPException(409, "Pengajuan sedang diproses oleh approver lain.")

    history_entry = {
        "action": "rejected",
        "at": now,
        "by_id": _uid(user),
        "by_email": actor_email,
        "by_name": _name(user),
        "note": notes,
    }
    updated = await db[COLLECTION].find_one_and_update(
        {"id": request["id"], "status": STATUS_PENDING},
        {
            "$set": {
                "status": STATUS_REJECTED,
                "decided_by_id": _uid(user),
                "decided_by_email": actor_email,
                "decided_by_name": _name(user),
                "decided_at": now,
                "decision_notes": notes,
                "decision_claim_id": claim_id,
                "updated_at": now,
            },
            "$push": {"history": history_entry},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        fresh = await get_request(db, request["id"])
        if fresh and fresh.get("status") == STATUS_REJECTED:
            return {"request": fresh, "order": None}
        raise HTTPException(409, "Pengajuan sudah diproses oleh approver lain.")
    updated.pop("_id", None)
    return {"request": updated, "order": None}


async def _claim_for_approve(db, *, request: dict, user: dict, notes: str, order: dict) -> Dict[str, Any]:
    """Obtain or resume approving claim. Returns {claimed, claim_id}."""
    now = now_iso()
    claim_id = str(uuid.uuid4())
    actor_email = _email(user)
    actor_name = _name(user)

    claimed = await db[COLLECTION].find_one_and_update(
        {"id": request["id"], "status": STATUS_PENDING},
        {
            "$set": {
                "status": STATUS_APPROVING,
                "decision_claim_id": claim_id,
                "decided_by_id": _uid(user),
                "decided_by_email": actor_email,
                "decided_by_name": actor_name,
                "decision_notes": notes,
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if claimed:
        claimed.pop("_id", None)
        return {"claimed": claimed, "claim_id": claim_id}

    fresh = await get_request(db, request["id"])
    if not fresh:
        raise HTTPException(404, "Pengajuan tidak ditemukan.")
    if fresh.get("status") == STATUS_APPROVED:
        return {"claimed": fresh, "claim_id": fresh.get("decision_claim_id"), "already_final": True}
    if fresh.get("status") != STATUS_APPROVING:
        raise HTTPException(409, "Pengajuan sudah diproses oleh approver lain.")

    holder = str(fresh.get("decided_by_email") or "").lower()
    applied = order_has_urgent_apply(order, request["id"])

    # Crash window after order update: any approver may complete finalize.
    if applied:
        return {"claimed": fresh, "claim_id": fresh.get("decision_claim_id") or claim_id}

    # Same actor resume (interruption before order update).
    if holder == actor_email.lower():
        return {"claimed": fresh, "claim_id": fresh.get("decision_claim_id") or claim_id}

    # Stale abandoned claim → release and reclaim.
    if claim_is_stale(fresh):
        await _release_claim(db, request["id"], fresh.get("decision_claim_id") or "")
        reclaimed = await db[COLLECTION].find_one_and_update(
            {"id": request["id"], "status": STATUS_PENDING},
            {
                "$set": {
                    "status": STATUS_APPROVING,
                    "decision_claim_id": claim_id,
                    "decided_by_id": _uid(user),
                    "decided_by_email": actor_email,
                    "decided_by_name": actor_name,
                    "decision_notes": notes,
                    "updated_at": now_iso(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if reclaimed:
            reclaimed.pop("_id", None)
            return {"claimed": reclaimed, "claim_id": claim_id}
        raise HTTPException(409, "Pengajuan sudah diproses oleh approver lain.")

    raise HTTPException(409, "Pengajuan sudah diproses oleh approver lain.")


async def _apply_order_urgent(
    db,
    *,
    request: dict,
    order: dict,
    user: dict,
    claim_id: str,
) -> dict:
    now = now_iso()
    actor_email = str(request.get("decided_by_email") or _email(user))
    actor_name = str(request.get("decided_by_name") or _name(user))
    existing_order = await db.orders.find_one({"id": order["id"]}, {"_id": 0})
    if not existing_order:
        await _release_claim(db, request["id"], claim_id)
        raise HTTPException(404, "PO tidak ditemukan")
    if existing_order.get("cancelled"):
        await _release_claim(db, request["id"], claim_id)
        raise HTTPException(400, "PO dibatalkan tidak dapat disetujui Urgent.")

    if order_has_urgent_apply(existing_order, request["id"]):
        # Ensure priority flag even if a partial write only pushed timeline.
        if str(existing_order.get("priority") or "").lower() != "urgent":
            fixed = await db.orders.find_one_and_update(
                {"id": order["id"], "cancelled": {"$ne": True}},
                {
                    "$set": {
                        "priority": "urgent",
                        "urgency_approved_by": actor_email,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if fixed:
                fixed.pop("_id", None)
                return fixed
        return existing_order

    timeline_entry = {
        "stage": existing_order.get("current_stage") or 0,
        "stage_label": "Urgent Approval",
        "at": now,
        "by_email": actor_email,
        "by_role": user.get("role"),
        "by_name": actor_name,
        "note": f"Urgent disetujui via pengajuan {request.get('id')}",
        "urgent_request_id": request["id"],
    }
    applied = await db.orders.find_one_and_update(
        {"id": order["id"], "cancelled": {"$ne": True}},
        {
            "$set": {
                "priority": "urgent",
                "urgency_approved_by": actor_email,
                "updated_at": now,
            },
            "$push": {"timeline": timeline_entry},
        },
        return_document=ReturnDocument.AFTER,
    )
    if not applied:
        await _release_claim(db, request["id"], claim_id)
        raise HTTPException(409, "Gagal menerapkan prioritas Urgent pada PO.")
    applied.pop("_id", None)
    return applied


async def _complete_approve_finalize(
    db,
    *,
    request: dict,
    claim_id: str,
    notes: str,
) -> dict:
    now = now_iso()
    history_entry = {
        "action": "approved",
        "at": now,
        "by_id": request.get("decided_by_id"),
        "by_email": request.get("decided_by_email"),
        "by_name": request.get("decided_by_name"),
        "note": notes or request.get("decision_notes") or "",
    }
    update: Dict[str, Any] = {
        "$set": {
            "status": STATUS_APPROVED,
            "decided_at": now,
            "updated_at": now,
            "decision_notes": notes or request.get("decision_notes") or "",
        }
    }
    if not history_has_action(request, "approved"):
        update["$push"] = {"history": history_entry}

    finalized = await db[COLLECTION].find_one_and_update(
        {
            "id": request["id"],
            "status": STATUS_APPROVING,
            "decision_claim_id": claim_id,
        },
        update,
        return_document=ReturnDocument.AFTER,
    )
    if finalized:
        finalized.pop("_id", None)
        return finalized

    # Race: another worker finalized, or claim_id drifted — re-read / heal.
    fresh = await get_request(db, request["id"])
    if fresh and fresh.get("status") == STATUS_APPROVED:
        return fresh
    if fresh and fresh.get("status") == STATUS_APPROVING and order_has_urgent_apply(
        await db.orders.find_one({"id": fresh.get("order_id")}, {"_id": 0}) or {},
        fresh["id"],
    ):
        # Claim id mismatch after crash: force finalize with stored claim.
        forced = await db[COLLECTION].find_one_and_update(
            {"id": fresh["id"], "status": STATUS_APPROVING},
            update,
            return_document=ReturnDocument.AFTER,
        )
        if forced:
            forced.pop("_id", None)
            return forced
    logger.error("urgent approve finalize failed request=%s claim=%s", request["id"], claim_id)
    raise HTTPException(409, "Gagal menyelesaikan persetujuan. Silakan coba lagi.")


async def _finalize_approve(db, *, request: dict, order: dict, user: dict, notes: str) -> Dict[str, Any]:
    hold = await _claim_for_approve(db, request=request, user=user, notes=notes, order=order)
    claimed = hold["claimed"]
    if hold.get("already_final"):
        ord_doc = await _ensure_order_matches_approved(db, request=claimed, order=order)
        return {"request": claimed, "order": ord_doc}

    claim_id = hold["claim_id"]
    order_doc = await _apply_order_urgent(
        db, request=claimed, order=order, user=user, claim_id=claim_id
    )
    # Refresh claimed after apply (still approving until finalize).
    claimed = await get_request(db, request["id"]) or claimed
    finalized = await _complete_approve_finalize(
        db, request=claimed, claim_id=claim_id, notes=notes
    )
    # Invariant: never leave approved + normal.
    if finalized.get("status") == STATUS_APPROVED:
        order_doc = await _ensure_order_matches_approved(db, request=finalized, order=order_doc)
    return {"request": finalized, "order": order_doc}


async def _release_claim(db, request_id: str, claim_id: str) -> None:
    q: Dict[str, Any] = {"id": request_id, "status": STATUS_APPROVING}
    if claim_id:
        q["decision_claim_id"] = claim_id
    await db[COLLECTION].update_one(
        q,
        {
            "$set": {
                "status": STATUS_PENDING,
                "decision_claim_id": None,
                "decided_by_id": None,
                "decided_by_email": None,
                "decided_by_name": None,
                "decision_notes": "",
                "updated_at": now_iso(),
            }
        },
    )


async def list_requests(
    db,
    *,
    status: Optional[str] = None,
    limit: int = 50,
) -> List[dict]:
    q: Dict[str, Any] = {}
    if status:
        q["status"] = status
    limit = max(1, min(int(limit or 50), 200))
    docs = await db[COLLECTION].find(q, {"_id": 0}).sort("created_at", -1).to_list(limit)
    return docs


async def resolve_notify_recipient_ids(db) -> List[str]:
    """Active users with explicit urgent.notify (opt-in). No role/Director/SA wildcard."""
    from permissions import opt_in_permission_granted

    users = await db.users.find(
        {"status": {"$ne": "inactive"}},
        {"_id": 0, "id": 1, "role": 1, "permissions_override": 1, "status": 1},
    ).to_list(2000)

    role_docs = await db.role_permissions.find({}, {"_id": 0, "role": 1, "permissions": 1}).to_list(200)
    role_override = {d["role"]: list(d.get("permissions") or []) for d in role_docs if d.get("role")}

    out: List[str] = []
    seen = set()
    for u in users:
        uid = str(u.get("id") or "").strip()
        if not uid or uid in seen:
            continue
        if str(u.get("status") or "").strip().lower() == "inactive":
            continue
        override = u.get("permissions_override") or {}
        if not opt_in_permission_granted(u.get("role"), override, PERM_NOTIFY, role_override or None):
            continue
        seen.add(uid)
        out.append(uid)
    return out


NOTIFY_REQUEST_DELIVERED = "notify_request_delivered"
NOTIFY_DECISION_DELIVERED = "notify_decision_delivered"


async def _mark_notify_delivery(db, request_id: str, field: str, ok: bool) -> None:
    await db[COLLECTION].update_one(
        {"id": str(request_id)},
        {"$set": {field: bool(ok), f"{field}_at": now_iso()}},
    )


async def emit_request_notifications(db, *, order: dict, request: dict) -> List[dict]:
    """Emit request notifications. Marks durable delivery flag; failures leave flag false for flush retry."""
    from services.notifications import create_notification

    rid = str((request or {}).get("id") or "")
    try:
        user_ids = await resolve_notify_recipient_ids(db)
        po = str(order.get("po_number") or request.get("po_number") or "-")
        title = "Pengajuan Urgent PO"
        body = f"PO {po} diajukan Urgent oleh {request.get('requested_by_name') or 'pengguna'}."
        link = f"/po/{order.get('id')}"
        created = []
        for uid in user_ids:
            if uid == str(request.get("requested_by_id") or ""):
                continue
            doc = await create_notification(
                db,
                user_id=uid,
                event_type=EVENT_REQUESTED,
                approval_id=request["id"],
                order_id=str(order.get("id") or ""),
                po_number=po,
                decision="requested",
                title=title,
                body=body,
                deep_link=link,
            )
            if doc:
                created.append(doc)
        if rid:
            await _mark_notify_delivery(db, rid, NOTIFY_REQUEST_DELIVERED, True)
        return created
    except Exception:
        logger.exception("emit_request_notifications failed")
        if rid:
            try:
                await _mark_notify_delivery(db, rid, NOTIFY_REQUEST_DELIVERED, False)
            except Exception:
                logger.exception("mark notify_request_delivered failed")
        return []


async def emit_decision_notification(db, *, order: dict, request: dict) -> Optional[dict]:
    """Emit decision notification. Marks durable delivery flag; failures leave flag false for flush retry."""
    from services.notifications import create_notification

    rid = str((request or {}).get("id") or "")
    try:
        uid = str(request.get("requested_by_id") or "").strip()
        if not uid:
            email = str(request.get("requested_by_email") or "").strip().lower()
            if email:
                u = await db.users.find_one({"email": email}, {"_id": 0, "id": 1})
                uid = str((u or {}).get("id") or "").strip()
        if not uid:
            if rid:
                await _mark_notify_delivery(db, rid, NOTIFY_DECISION_DELIVERED, True)
            return None
        status = request.get("status")
        po = str(order.get("po_number") or request.get("po_number") or "-") if order else str(request.get("po_number") or "-")
        order_id = str((order or {}).get("id") or request.get("order_id") or "")
        if status == STATUS_APPROVED:
            title = "Pengajuan Urgent disetujui"
            body = f"PO {po} disetujui Urgent oleh {request.get('decided_by_name') or 'approver'}."
            if request.get("decision_notes"):
                body += f" Catatan: {request['decision_notes']}"
            event = EVENT_APPROVED
            decision = "approved"
        elif status == STATUS_REJECTED:
            title = "Pengajuan Urgent ditolak"
            body = f"PO {po} ditolak. Alasan: {request.get('decision_notes') or '-'}."
            event = EVENT_REJECTED
            decision = "rejected"
        else:
            return None
        doc = await create_notification(
            db,
            user_id=uid,
            event_type=event,
            approval_id=request["id"],
            order_id=order_id,
            po_number=po,
            decision=decision,
            title=title,
            body=body,
            deep_link=f"/po/{order_id}" if order_id else "/",
        )
        if rid:
            await _mark_notify_delivery(db, rid, NOTIFY_DECISION_DELIVERED, True)
        return doc
    except Exception:
        logger.exception("emit_decision_notification failed")
        if rid:
            try:
                await _mark_notify_delivery(db, rid, NOTIFY_DECISION_DELIVERED, False)
            except Exception:
                logger.exception("mark notify_decision_delivered failed")
        return None


async def flush_urgent_notifications(db, *, request: Optional[dict], order: Optional[dict] = None) -> Optional[dict]:
    """Idempotent recovery for failed in-app notification delivery.

    Retries emit when durable delivery flags are missing/false. create_notification
    dedupe_key prevents duplicate inbox rows. Never raises to callers.
    """
    if not request or not request.get("id"):
        return request
    try:
        fresh = await get_request(db, request["id"]) or request
        ord_doc = order
        if ord_doc is None and fresh.get("order_id"):
            ord_doc = await db.orders.find_one({"id": fresh["order_id"]}, {"_id": 0})
        ord_doc = ord_doc or {}

        if not fresh.get(NOTIFY_REQUEST_DELIVERED):
            await emit_request_notifications(db, order=ord_doc, request=fresh)
            fresh = await get_request(db, request["id"]) or fresh

        if fresh.get("status") in (STATUS_APPROVED, STATUS_REJECTED) and not fresh.get(NOTIFY_DECISION_DELIVERED):
            await emit_decision_notification(db, order=ord_doc, request=fresh)
            fresh = await get_request(db, request["id"]) or fresh
        return fresh
    except Exception:
        logger.exception("flush_urgent_notifications failed request=%s", request.get("id"))
        return request
