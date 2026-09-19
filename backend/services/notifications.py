"""Minimal in-app notifications.

Events:
  - customer design approved / rejected → Designer/CRM (+ explicit members)
  - loading qty mismatch vs SJ → CRM (branch-aware)
  - surat jalan created → Helper, Driver, Kepala Produksi (branch-aware)

Side effect only: never rolls back the triggering mutation.
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
ROLE_HELPER = "helper"
ROLE_DRIVER = "driver"
ROLE_HEAD_PRODUCTION = "head_production"
RECIPIENT_ROLES = (ROLE_DESIGNER, ROLE_CRM)
SJ_CREATED_RECIPIENT_ROLES = (ROLE_HELPER, ROLE_DRIVER, ROLE_HEAD_PRODUCTION)

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


def _created_at_desc_key(row: Dict[str, Any]) -> str:
    return str((row or {}).get("created_at") or "")


def _is_unread_read_at(value: Any) -> bool:
    return value is None or value == ""


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
    coll = db[COLLECTION]
    projection = {"_id": 0}

    # Unread-first MUST happen before limit so an old unread is not clipped
    # by newer reads. Two bounded finds stay index-compatible with
    # (user_id, created_at) — do not fetch the full inbox and sort in Python.
    unread_missing = await coll.find(
        {"user_id": uid, "read_at": None},
        projection,
    ).sort("created_at", -1).to_list(lim)

    unread_empty: List[Dict[str, Any]] = []
    if len(unread_missing) < lim:
        unread_empty = await coll.find(
            {"user_id": uid, "read_at": ""},
            projection,
        ).sort("created_at", -1).to_list(lim - len(unread_missing))

    unread = list(unread_missing)
    if unread_empty:
        unread.extend(unread_empty)
        if unread_missing:
            unread = sorted(unread, key=_created_at_desc_key, reverse=True)[:lim]

    rows = list(unread)
    remaining = lim - len(rows)
    if remaining > 0:
        # $ne: None includes "" on some matchers; over-fetch by the empty-unread
        # count (already bounded) so those rows can be skipped.
        read_rows = await coll.find(
            {"user_id": uid, "read_at": {"$ne": None}},
            projection,
        ).sort("created_at", -1).to_list(remaining + len(unread_empty))
        seen = {r.get("id") for r in rows}
        for row in read_rows:
            if row.get("id") in seen or _is_unread_read_at(row.get("read_at")):
                continue
            rows.append(row)
            if len(rows) >= lim:
                break

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


# ---------------------------------------------------------------------------
# Loading qty discrepancy → CRM (branch-aware role recipients)
# ---------------------------------------------------------------------------

EVENT_LOADING_QTY_MISMATCH = "loading_qty_mismatch"
EVENT_SURAT_JALAN_CREATED = "surat_jalan_created"


async def resolve_role_recipient_user_ids_for_branch(
    db,
    role: str,
    branch_id: Optional[str] = None,
) -> List[str]:
    """Active users for one canonical role, optionally scoped to branch_id."""
    want = str(role or "").strip()
    if not want:
        return []
    query: Dict[str, Any] = {"role": want, "status": {"$ne": "inactive"}}
    bid = str(branch_id or "").strip()
    if bid:
        query["$or"] = [
            {"branch_id": bid},
            {"cabang_id": bid},
            {"branch_id": {"$exists": False}},
            {"branch_id": None},
            {"branch_id": ""},
        ]
    cursor = db.users.find(query, {"_id": 0, "id": 1, "branch_id": 1, "cabang_id": 1})
    users = await cursor.to_list(500)
    seen: Set[str] = set()
    out: List[str] = []
    for u in users:
        uid = str((u or {}).get("id") or "").strip()
        if not uid or uid in seen:
            continue
        if bid:
            ub = str((u or {}).get("branch_id") or (u or {}).get("cabang_id") or "").strip()
            if ub and ub != bid:
                continue
        seen.add(uid)
        out.append(uid)
    return out


async def resolve_crm_recipient_user_ids_for_branch(db, branch_id: Optional[str] = None) -> List[str]:
    """Active CRM-role users, optionally scoped to branch_id when users carry branch."""
    return await resolve_role_recipient_user_ids_for_branch(db, ROLE_CRM, branch_id)


async def emit_loading_qty_discrepancy_notifications(
    db,
    *,
    order: Dict[str, Any],
    discrepancy: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> List[Dict[str, Any]]:
    """Notify CRM after a genuine physical qty mismatch vs SJ.

    Idempotent via dedupe_key on discrepancy id. Never raises to caller.
    """
    log = logger or logging.getLogger("notifications")
    try:
        disc_id = str((discrepancy or {}).get("id") or "").strip()
        if not disc_id:
            return []
        branch_id = (
            (discrepancy or {}).get("branch_id")
            or order.get("branch_id")
            or order.get("cabang_id")
        )
        user_ids = await resolve_crm_recipient_user_ids_for_branch(db, branch_id)
        if not user_ids:
            log.info("loading_qty_mismatch: no CRM recipients for branch=%s", branch_id)
            return []

        po = str(order.get("po_number") or order.get("number") or "").strip() or "-"
        customer = order.get("customer") or order.get("customer_name") or order.get("outlet_name") or ""
        sj = str(discrepancy.get("surat_jalan_id") or "").strip()
        lines = discrepancy.get("lines") or []
        parts = []
        for ln in lines[:5]:
            if not isinstance(ln, dict):
                continue
            name = ln.get("name") or f"line {ln.get('line_idx')}"
            sj_q = ln.get("qty_sj")
            act = ln.get("qty_actual")
            cls = ln.get("classification") or ""
            diff = abs(int(ln.get("difference") or 0))
            parts.append(f"{name} — SJ {sj_q}, fisik {act}, {cls} {diff}.")
        body_lines = " ".join(parts) or "Terdapat selisih qty fisik terhadap Surat Jalan."
        note = discrepancy.get("note")
        title = "Qty Barang Tidak Sesuai SJ"
        body = f"PO {po}"
        if customer:
            body += f" / {customer}"
        body += f". SJ {sj}. {body_lines}"
        if note:
            body += f" Catatan: {note}"
        reporter = discrepancy.get("reported_by_name") or discrepancy.get("reported_by") or ""
        if reporter:
            body += f" Dilaporkan oleh {reporter}."
        order_id = str(order.get("id") or "")
        link = deep_link_for_order(order_id)

        created: List[Dict[str, Any]] = []
        for uid in user_ids:
            doc = await create_notification(
                db,
                user_id=uid,
                event_type=EVENT_LOADING_QTY_MISMATCH,
                approval_id=disc_id,
                order_id=order_id,
                po_number=po,
                decision="qty_mismatch",
                title=title,
                body=body,
                deep_link=link,
            )
            if doc:
                created.append(doc)
        return created
    except Exception:
        log.exception("emit_loading_qty_discrepancy_notifications failed")
        return []


def canonical_sj_id_from_shipment(shipment: Optional[Dict[str, Any]]) -> str:
    if not isinstance(shipment, dict):
        return ""
    return str(shipment.get("id") or shipment.get("sales_delivery_id") or "").strip()


def canonical_sj_number_from_shipment(shipment: Optional[Dict[str, Any]]) -> str:
    if not isinstance(shipment, dict):
        return ""
    return str(shipment.get("number") or shipment.get("sj_number") or "").strip()


def _order_branch_id(order: Optional[Dict[str, Any]]) -> str:
    if not isinstance(order, dict):
        return ""
    return str(order.get("branch_id") or order.get("cabang_id") or "").strip()


def _assigned_driver_user_id(order: Optional[Dict[str, Any]]) -> str:
    if not isinstance(order, dict):
        return ""
    lc = order.get("loading_checklist") if isinstance(order.get("loading_checklist"), dict) else {}
    return str(lc.get("driver_user_id") or order.get("driver_user_id") or "").strip()


def build_sj_created_notification_content(
    *,
    order: Dict[str, Any],
    shipment: Dict[str, Any],
    role: str,
) -> Dict[str, str]:
    po = str(order.get("po_number") or order.get("number") or "").strip() or "-"
    customer = order.get("customer_name") or order.get("customer") or order.get("outlet_name") or ""
    sj = canonical_sj_number_from_shipment(shipment)
    role_label = {
        ROLE_HELPER: "Helper",
        ROLE_DRIVER: "Driver",
        ROLE_HEAD_PRODUCTION: "Kepala Produksi",
    }.get(role, role)
    title = f"Surat Jalan dibuat — {role_label}"
    body = f"PO {po}"
    if customer:
        body += f" / {customer}"
    if sj:
        body += f". SJ {sj}."
    else:
        body += "."
    return {"title": title, "body": body}


async def resolve_sj_created_recipient_ids_for_role(
    db,
    *,
    order: Dict[str, Any],
    role: str,
) -> List[str]:
    """Helper / Kepala Produksi: active role users at branch. Driver: assigned user, else role users."""
    branch_id = _order_branch_id(order)
    if role == ROLE_DRIVER:
        assigned = _assigned_driver_user_id(order)
        if assigned:
            found = await resolve_explicit_recipient_user_ids(db, [assigned])
            if found:
                return found
    return await resolve_role_recipient_user_ids_for_branch(db, role, branch_id)


async def emit_surat_jalan_created_notifications(
    db,
    *,
    order: Dict[str, Any],
    shipment: Dict[str, Any],
    logger: Optional[logging.Logger] = None,
) -> List[Dict[str, Any]]:
    """Notify Helper, Driver, and Kepala Produksi after a valid Surat Jalan is persisted.

    Requires canonical SJ id AND number — same rule as PWA Jadwal Pengiriman visibility.
    Does not infer from packing, loading, production, or estimation date.
    Idempotent via dedupe_key on shipment id + user. Never raises to caller.
    """
    log = logger or logging.getLogger("notifications")
    try:
        shipment_id = canonical_sj_id_from_shipment(shipment)
        sj_number = canonical_sj_number_from_shipment(shipment)
        if not shipment_id or not sj_number:
            return []
        order_id = str((order or {}).get("id") or "").strip()
        po = str((order or {}).get("po_number") or "").strip() or None
        link = deep_link_for_order(order_id) if order_id else "/m/delivery-schedule"

        created: List[Dict[str, Any]] = []
        for role in SJ_CREATED_RECIPIENT_ROLES:
            user_ids = await resolve_sj_created_recipient_ids_for_role(db, order=order or {}, role=role)
            if not user_ids:
                continue
            content = build_sj_created_notification_content(
                order=order or {},
                shipment=shipment,
                role=role,
            )
            for uid in user_ids:
                doc = await create_notification(
                    db,
                    user_id=uid,
                    event_type=EVENT_SURAT_JALAN_CREATED,
                    approval_id=shipment_id,
                    order_id=order_id,
                    po_number=po,
                    decision=role,
                    title=content["title"],
                    body=content["body"],
                    deep_link=link,
                )
                if doc:
                    created.append(doc)
        return created
    except Exception:
        log.exception("emit_surat_jalan_created_notifications failed")
        return []
