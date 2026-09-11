"""Minimal in-app notifications — Customer Design Approval only.

Scope (locked):
  - Events: customer design approved / rejected
  - Recipients: active Designer/CRM roles UNION explicitly selected members
  - Settings: app_settings key customer_design_approval_notification
  - Side effect only: never rolls back customer approval mutations

Not a StageFlow notification framework.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set

from pymongo.errors import DuplicateKeyError

COLLECTION = "notifications"
SETTINGS_KEY = "customer_design_approval_notification"

EVENT_APPROVED = "customer_design_approved"
EVENT_REJECTED = "customer_design_rejected"

ROLE_DESIGNER = "designer"
ROLE_CRM = "crm"
RECIPIENT_ROLES = (ROLE_DESIGNER, ROLE_CRM)

DEFAULT_SETTINGS = {
    "key": SETTINGS_KEY,
    "enabled": True,
    "notify_designer": True,
    "notify_crm": True,
    "additional_recipient_user_ids": [],
    "updated_at": None,
    "updated_by": None,
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_settings() -> Dict[str, Any]:
    return dict(DEFAULT_SETTINGS)


def normalize_additional_recipient_user_ids(raw: Any) -> List[str]:
    """Canonical user id list. Missing/invalid → []. Dedupes preserving order."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return []
    seen: Set[str] = set()
    out: List[str] = []
    for item in raw:
        if isinstance(item, dict):
            uid = str(item.get("id") or item.get("user_id") or "").strip()
        else:
            uid = str(item or "").strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def sanitize_settings(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = default_settings()
    if not isinstance(doc, dict):
        return base
    out = dict(base)
    if "enabled" in doc and doc["enabled"] is not None:
        out["enabled"] = bool(doc["enabled"])
    if "notify_designer" in doc and doc["notify_designer"] is not None:
        out["notify_designer"] = bool(doc["notify_designer"])
    if "notify_crm" in doc and doc["notify_crm"] is not None:
        out["notify_crm"] = bool(doc["notify_crm"])
    # Backward compat: missing field → []
    out["additional_recipient_user_ids"] = normalize_additional_recipient_user_ids(
        doc.get("additional_recipient_user_ids")
    )
    out["updated_at"] = doc.get("updated_at")
    out["updated_by"] = doc.get("updated_by")
    out["key"] = SETTINGS_KEY
    return out


async def get_notification_settings(db) -> Dict[str, Any]:
    """Pure read. Missing doc → in-memory defaults (no write)."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return sanitize_settings(doc)


async def _filter_existing_user_ids(db, user_ids: Sequence[str]) -> List[str]:
    """Keep only IDs that exist in users (active or inactive). Preserve order."""
    clean = normalize_additional_recipient_user_ids(list(user_ids))
    if not clean:
        return []
    cursor = db.users.find({"id": {"$in": clean}}, {"_id": 0, "id": 1})
    rows = await cursor.to_list(len(clean) + 50)
    existing = {str((u or {}).get("id") or "").strip() for u in rows}
    existing.discard("")
    return [uid for uid in clean if uid in existing]


async def update_notification_settings(
    db,
    payload: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Payload tidak valid")
    if "enabled" not in payload:
        raise ValueError("Field 'enabled' wajib")
    if "notify_designer" not in payload:
        raise ValueError("Field 'notify_designer' wajib")
    if "notify_crm" not in payload:
        raise ValueError("Field 'notify_crm' wajib")
    if not isinstance(payload["enabled"], bool):
        raise ValueError("Field 'enabled' harus boolean")
    if not isinstance(payload["notify_designer"], bool):
        raise ValueError("Field 'notify_designer' harus boolean")
    if not isinstance(payload["notify_crm"], bool):
        raise ValueError("Field 'notify_crm' harus boolean")

    if "additional_recipient_user_ids" in payload:
        raw_ids = payload["additional_recipient_user_ids"]
        if raw_ids is None:
            raw_ids = []
        if not isinstance(raw_ids, list):
            raise ValueError("Field 'additional_recipient_user_ids' harus list")
        for item in raw_ids:
            if isinstance(item, dict):
                raise ValueError(
                    "Field 'additional_recipient_user_ids' hanya menerima canonical user id (string)"
                )
            if item is not None and not isinstance(item, str):
                raise ValueError(
                    "Field 'additional_recipient_user_ids' hanya menerima canonical user id (string)"
                )
        # Dedupe input; drop nonexistent IDs (ignore invalid refs)
        candidate_ids = normalize_additional_recipient_user_ids(raw_ids)
        additional_ids = await _filter_existing_user_ids(db, candidate_ids)
    else:
        # Older clients: omit field → persist empty explicit list
        additional_ids = []

    actor = None
    if isinstance(user, dict):
        actor = {
            "id": str(user.get("id") or user.get("user_id") or user.get("sub") or ""),
            "name": user.get("name") or user.get("email") or "",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }

    saved = {
        "key": SETTINGS_KEY,
        "enabled": payload["enabled"],
        "notify_designer": payload["notify_designer"],
        "notify_crm": payload["notify_crm"],
        "additional_recipient_user_ids": additional_ids,
        "updated_at": now_iso(),
        "updated_by": actor,
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return sanitize_settings(saved)


def enabled_recipient_roles(settings: Dict[str, Any]) -> List[str]:
    roles: List[str] = []
    if settings.get("notify_designer", True):
        roles.append(ROLE_DESIGNER)
    if settings.get("notify_crm", True):
        roles.append(ROLE_CRM)
    return roles


def user_canonical_id(user: Optional[Dict[str, Any]]) -> str:
    if not isinstance(user, dict):
        return ""
    return str(user.get("id") or user.get("user_id") or user.get("sub") or "").strip()


def dedupe_key(event_type: str, approval_id: str, user_id: str) -> str:
    return f"{event_type}:{approval_id}:{user_id}"


def deep_link_for_order(order_id: str) -> str:
    return f"/m/orders/{order_id}"


def _public_notification(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": doc.get("id"),
        "event_type": doc.get("event_type"),
        "title": doc.get("title"),
        "body": doc.get("body"),
        "order_id": doc.get("order_id"),
        "po_number": doc.get("po_number"),
        "approval_id": doc.get("approval_id"),
        "decision": doc.get("decision"),
        "deep_link": doc.get("deep_link"),
        "read_at": doc.get("read_at"),
        "created_at": doc.get("created_at"),
        "is_read": bool(doc.get("read_at")),
    }


async def resolve_recipient_user_ids(db, roles: Sequence[str]) -> List[str]:
    """Active users whose canonical role is in `roles`. Deduped by user id."""
    clean_roles = [r for r in roles if r in RECIPIENT_ROLES]
    if not clean_roles:
        return []
    cursor = db.users.find(
        {"role": {"$in": list(clean_roles)}, "status": {"$ne": "inactive"}},
        {"_id": 0, "id": 1},
    )
    users = await cursor.to_list(500)
    seen: Set[str] = set()
    out: List[str] = []
    for u in users:
        uid = str((u or {}).get("id") or "").strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


async def resolve_explicit_recipient_user_ids(db, user_ids: Sequence[str]) -> List[str]:
    """Active accounts matching explicit canonical IDs. Inactive/unknown skipped."""
    clean = normalize_additional_recipient_user_ids(list(user_ids or []))
    if not clean:
        return []
    cursor = db.users.find(
        {"id": {"$in": clean}, "status": {"$ne": "inactive"}},
        {"_id": 0, "id": 1},
    )
    users = await cursor.to_list(len(clean) + 50)
    found = {str((u or {}).get("id") or "").strip() for u in users}
    found.discard("")
    return [uid for uid in clean if uid in found]


async def resolve_final_recipient_user_ids(db, settings: Dict[str, Any]) -> List[str]:
    """Role recipients UNION explicit members, deduped by canonical user id."""
    roles = enabled_recipient_roles(settings or {})
    role_ids = await resolve_recipient_user_ids(db, roles)
    explicit_ids = await resolve_explicit_recipient_user_ids(
        db, (settings or {}).get("additional_recipient_user_ids") or [],
    )
    seen: Set[str] = set()
    out: List[str] = []
    for uid in list(role_ids) + list(explicit_ids):
        if not uid or uid in seen:
            continue
        seen.add(uid)
        out.append(uid)
    return out


def build_notification_content(
    *,
    decision: str,
    order: Dict[str, Any],
    approval: Dict[str, Any],
) -> Dict[str, str]:
    po = str(order.get("po_number") or approval.get("po_number") or "").strip() or "-"
    customer = order.get("customer")
    customer_name = str(customer).strip() if customer else ""
    reject_reason = (approval.get("reject_reason") or "").strip() if decision == "rejected" else ""

    if decision == "approved":
        title = "Design disetujui customer"
        body = f"Customer menyetujui design untuk PO {po}"
        if customer_name:
            body = f"Customer {customer_name} menyetujui design untuk PO {po}"
    else:
        title = "Design ditolak customer"
        body = f"Customer menolak design untuk PO {po}"
        if customer_name:
            body = f"Customer {customer_name} menolak design untuk PO {po}"
        if reject_reason:
            body = f"{body}. Alasan: {reject_reason}"
    return {"title": title, "body": body}


async def create_notification(
    db,
    *,
    user_id: str,
    event_type: str,
    approval_id: str,
    order_id: str,
    po_number: Optional[str],
    decision: str,
    title: str,
    body: str,
    deep_link: str,
) -> Optional[Dict[str, Any]]:
    """Insert one notification. Duplicate dedupe_key → return existing (idempotent)."""
    uid = str(user_id or "").strip()
    if not uid:
        return None
    key = dedupe_key(event_type, str(approval_id), uid)
    doc = {
        "id": str(uuid.uuid4()),
        "user_id": uid,
        "event_type": event_type,
        "dedupe_key": key,
        "title": title,
        "body": body,
        "order_id": order_id,
        "po_number": po_number,
        "approval_id": approval_id,
        "decision": decision,
        "deep_link": deep_link,
        "read_at": None,
        "created_at": now_iso(),
    }
    try:
        await db[COLLECTION].insert_one(dict(doc))
        return doc
    except DuplicateKeyError:
        existing = await db[COLLECTION].find_one({"dedupe_key": key}, {"_id": 0})
        return existing


async def emit_customer_design_approval_notifications(
    db,
    *,
    order: Dict[str, Any],
    approval: Dict[str, Any],
    decision: str,
    logger: Optional[logging.Logger] = None,
) -> List[Dict[str, Any]]:
    """Create notifications for an authoritative customer design decision.

    Never raises to caller for operational failures — logs and returns [].
    """
    log = logger or logging.getLogger("notifications")
    try:
        decision_norm = str(decision or "").strip().lower()
        if decision_norm not in ("approved", "rejected"):
            return []
        settings = await get_notification_settings(db)
        if not settings.get("enabled", True):
            return []
        user_ids = await resolve_final_recipient_user_ids(db, settings)
        if not user_ids:
            return []

        event_type = EVENT_APPROVED if decision_norm == "approved" else EVENT_REJECTED
        content = build_notification_content(decision=decision_norm, order=order, approval=approval)
        order_id = str(order.get("id") or approval.get("order_id") or "")
        approval_id = str(approval.get("id") or "")
        po_number = order.get("po_number") or approval.get("po_number")
        link = deep_link_for_order(order_id)

        created: List[Dict[str, Any]] = []
        for uid in user_ids:
            doc = await create_notification(
                db,
                user_id=uid,
                event_type=event_type,
                approval_id=approval_id,
                order_id=order_id,
                po_number=po_number,
                decision=decision_norm,
                title=content["title"],
                body=content["body"],
                deep_link=link,
            )
            if doc:
                created.append(doc)
        return created
    except Exception:
        log.exception(
            "customer_design_approval_notification_failed approval_id=%s decision=%s",
            (approval or {}).get("id"),
            decision,
        )
        return []


async def list_notifications_for_user(
    db,
    user_id: str,
    *,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    uid = str(user_id or "").strip()
    if not uid:
        return []
    lim = max(1, min(int(limit or 50), 100))
    rows = await db[COLLECTION].find(
        {"user_id": uid},
        {"_id": 0},
    ).sort("created_at", -1).to_list(lim)
    return [_public_notification(r) for r in rows]


async def mark_notification_read(db, user_id: str, notification_id: str) -> Optional[Dict[str, Any]]:
    uid = str(user_id or "").strip()
    nid = str(notification_id or "").strip()
    if not uid or not nid:
        return None
    existing = await db[COLLECTION].find_one({"id": nid, "user_id": uid}, {"_id": 0})
    if not existing:
        return None
    if existing.get("read_at"):
        return _public_notification(existing)
    read_at = now_iso()
    await db[COLLECTION].update_one(
        {"id": nid, "user_id": uid, "read_at": None},
        {"$set": {"read_at": read_at}},
    )
    existing["read_at"] = read_at
    return _public_notification(existing)
