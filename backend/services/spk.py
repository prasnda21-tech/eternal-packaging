"""SPK Antar Divisi — Surat Perintah Kerja.

Collections
----------
spk_orders
    Master document. Milestones are embedded so weight, actuals, cached
    progress, and doc_version change in one atomic update. A separate
    milestone collection would need a multi-document transaction that this
    codebase does not use for similar workflows (product requests, purchase
    docs).

spk_progress_events
    Append-only audit stream. Progress updates and lifecycle events
    (CREATE, PUBLISH, ACKNOWLEDGE, START, VERIFICATION_REQUEST,
    VERIFY_APPROVE, VERIFY_REVISION, CANCEL) live here and are never
    rewritten by a later status change.

File bytes go through services.storage (existing object storage) and the
shared `files` collection. This module does not introduce a storage backend.

Weight rule
-----------
On publish, milestone weights must sum to exactly 100.00 (2 decimal places).
Drafts may be saved before the total is 100. Overall progress is always

    SUM(milestone_progress * weight) / total_weight

capped at 100. The cached `progress_percent` is recomputed on every write
and is not an editable field.

Numbering
---------
SPK-YYYYMM-####, allocated inside this module. The shared daily helper
(`PREFIX-YYYYMMDD-###`) stays on purchase and other documents. The sequence
resets each month. `spk_number` unique index plus insert retry is what
prevents duplicates.

Progress consistency
--------------------
Production Mongo in this codebase is not used with multi-document
transactions (standalone `MONGO_URL`, no `start_session` / `with_transaction`
on business writes). Progress therefore uses a recovery protocol:

1. Insert one reserved command keyed by `idempotency_key` (not a canonical event).
2. CAS the order once, stamped with `progress_commit.idempotency_key`.
3. Finalize that same command to `commit_state=applied`.
4. Notify after the canonical event exists. Notification dedupe is the event id.

A retry of the same key converges: apply at most once, one applied event,
one notification. Reserved and rejected commands are omitted from the timeline.
Reads never repair this state.

Deadline state
--------------
ON_TRACK / DUE_SOON / OVERDUE is derived at read time in Asia/Jakarta.
Completed and cancelled SPK do not carry an overdue warning.
The document is not rewritten each day.

Permissions
-----------
API visibility is enforced here. Frontend hiding is not sufficient.
Builtin role defaults grant SPK keys. Roles already saved in
role_permissions are not rewritten.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.employee_admin import user_division_ids
from services.notifications import create_notification
from services.product_requests import load_actor_branch
from services.purchase_common import (
    actor_snapshot,
    get_named_lock,
    insert_with_unique_number,
)

ORDERS = "spk_orders"
EVENTS = "spk_progress_events"
IDEMPOTENCY = "spk_idempotency"
# Retries of the same key must still see the stored result. Ninety days is
# long enough for a human or client retry and short enough that the
# collection does not grow without a bound. The TTL index is created
# idempotently by ensure_indexes; it does not rewrite existing SPK documents.
IDEMPOTENCY_RETENTION_DAYS = 90
FILES_KIND = "spk"
PREFIX = "SPK"

WIB = timezone(timedelta(hours=7))
WEIGHT_TOTAL = Decimal("100.00")
DUE_SOON_DAYS = 3

STATUS_DRAFT = "DRAFT"
STATUS_PUBLISHED = "PUBLISHED"
STATUS_ACKNOWLEDGED = "ACKNOWLEDGED"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_WAITING = "WAITING_VERIFICATION"
STATUS_REVISION = "REVISION_REQUIRED"
STATUS_COMPLETED = "COMPLETED"
STATUS_CANCELLED = "CANCELLED"
STATUS_RETURNED = "RETURNED"

WORKFLOW_STATUSES = (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    STATUS_ACKNOWLEDGED,
    STATUS_IN_PROGRESS,
    STATUS_WAITING,
    STATUS_REVISION,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
    STATUS_RETURNED,
)
TERMINAL = {STATUS_COMPLETED, STATUS_CANCELLED}
ACTIVE_STATUSES = {
    STATUS_PUBLISHED,
    STATUS_ACKNOWLEDGED,
    STATUS_IN_PROGRESS,
    STATUS_REVISION,
}
PROGRESS_STATUSES = {STATUS_IN_PROGRESS, STATUS_REVISION}

PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")
MILESTONE_TYPES = ("QUANTITY", "CHECKLIST", "APPROVAL")
MILESTONE_STATUSES = ("PENDING", "IN_PROGRESS", "COMPLETED")

EVENT_CREATE = "CREATE"
EVENT_PUBLISH = "PUBLISH"
EVENT_ACKNOWLEDGE = "ACKNOWLEDGE"
EVENT_START = "START"
EVENT_PROGRESS = "PROGRESS_UPDATE"
EVENT_VERIFY_REQUEST = "VERIFICATION_REQUEST"
EVENT_VERIFY_APPROVE = "VERIFY_APPROVE"
EVENT_VERIFY_REVISION = "VERIFY_REVISION"
EVENT_CANCEL = "CANCEL"
EVENT_EDIT = "EDIT"
EVENT_REPUBLISH = "REPUBLISH"
EVENT_RETURN = "RETURN"
EVENT_NOTE = "NOTE"
EVENT_EVIDENCE = "EVIDENCE"
EVENT_CORRECTION = "CORRECTION"
EVENT_PIC_CHANGE = "PIC_CHANGE"

PERM_CREATE = "spk.create"
PERM_VIEW_OWN = "spk.view_own"
PERM_VIEW_DIVISION = "spk.view_division"
PERM_VIEW_ALL = "spk.view_all"
PERM_ASSIGN = "spk.assign"
PERM_PROGRESS = "spk.update_progress"
PERM_COMMENT = "spk.comment"
PERM_CORRECT = "spk.correct"
PERM_VERIFY = "spk.verify"
PERM_CANCEL = "spk.cancel"
PERM_MANAGE = "spk.manage"
PIC_ELIGIBLE_PERMS = {
    PERM_VIEW_OWN,
    PERM_VIEW_DIVISION,
    PERM_VIEW_ALL,
    PERM_PROGRESS,
    PERM_MANAGE,
}
VIEW_PERMS = (PERM_VIEW_OWN, PERM_VIEW_DIVISION, PERM_VIEW_ALL, PERM_MANAGE, PERM_CREATE)

SPK_DIVISIONS = (
    ("penjualan", "Penjualan"),
    ("direksi", "Direksi"),
    ("operasional", "Operasional"),
    ("kepala_produksi", "Kepala Produksi"),
    ("designer", "Designer"),
    ("screener", "Screener"),
    ("helper", "Helper"),
    ("operator", "Operator"),
    ("packing", "Packing"),
    ("surat_jalan", "Surat Jalan"),
    ("loading", "Loading"),
    ("pengiriman", "Pengiriman"),
    ("crm", "CRM"),
)
SPK_DIVISION_IDS = {item[0] for item in SPK_DIVISIONS}
SPK_DIVISION_LABELS = {item[0]: item[1] for item in SPK_DIVISIONS}

# Role → SPK division. Does not change HR eligibility in employee_admin.
SPK_ROLE_DIVISIONS = {
    "direktur_utama": ("direksi",),
    "admin": ("direksi",),
    "holding_team": ("direksi",),
    "crm": ("penjualan",),
    "sales_eternal": ("penjualan",),
    "head_operations": ("operasional",),
    "head_production": ("kepala_produksi",),
    "designer": ("designer",),
    "screener": ("screener",),
    "helper": ("helper",),
    "operator": ("operator",),
    "driver": ("pengiriman", "loading", "surat_jalan"),
}

_today_override: Optional[date] = None
# Test seam. Production leaves this None. Points: reserve, mutate, canonical.
_progress_crash_after: Optional[str] = None

COMMIT_RESERVED = "reserved"
COMMIT_APPLIED = "applied"
COMMIT_REJECTED = "rejected"
_SPK_NUMBER = re.compile(r"^SPK-(\d{6})-(\d{4})$")


class ProgressCrash(RuntimeError):
    """Simulated crash between progress protocol steps. Tests only."""


def _crash_progress(point: str) -> None:
    if _progress_crash_after == point:
        raise ProgressCrash(point)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def today_wib(now: Optional[datetime] = None) -> date:
    if _today_override is not None and now is None:
        return _today_override
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(WIB).date()


def _q2(value: Any) -> Decimal:
    try:
        dec = Decimal(str(value if value is not None and value != "" else "0"))
    except Exception as exc:
        raise HTTPException(400, "Nilai angka tidak valid.") from exc
    if not dec.is_finite():
        raise HTTPException(400, "Nilai angka tidak valid.")
    return dec.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _dec_str(value: Decimal) -> str:
    return f"{_q2(value):.2f}"


def _clean_text(value: Any, *, max_len: int, field: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise HTTPException(400, f"{field} wajib diisi.")
    if len(text) > max_len:
        raise HTTPException(400, f"{field} maksimal {max_len} karakter.")
    return text


def _parse_date(raw: Any, field: str, *, required: bool) -> Optional[str]:
    text = str(raw or "").strip()[:10]
    if not text:
        if required:
            raise HTTPException(400, f"{field} wajib diisi.")
        return None
    try:
        date.fromisoformat(text)
    except ValueError as exc:
        raise HTTPException(400, f"{field} wajib berformat YYYY-MM-DD.") from exc
    return text


def _version(raw: Any) -> int:
    try:
        version = int(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "doc_version wajib diisi.") from exc
    if version < 1:
        raise HTTPException(400, "doc_version tidak valid.")
    return version


def actor_user_id(user: Optional[dict]) -> str:
    return str(actor_snapshot(user).get("id") or "").strip()


def division_label(division_id: str) -> str:
    return SPK_DIVISION_LABELS.get(str(division_id or ""), str(division_id or ""))


def spk_user_division_ids(user: Optional[dict]) -> set:
    """Divisions used only for SPK visibility. Does not alter HR division rules."""
    user = user or {}
    ids = set()
    for raw in user_division_ids(user):
        key = "pengiriman" if raw == "terkirim" else str(raw)
        if key in SPK_DIVISION_IDS:
            ids.add(key)
    role = str(user.get("role") or "")
    for key in SPK_ROLE_DIVISIONS.get(role, ()):
        ids.add(key)
    extra = user.get("divisions") or user.get("division_ids") or []
    if isinstance(extra, str):
        extra = [extra]
    for item in extra or []:
        key = str(item or "").strip()
        if key in SPK_DIVISION_IDS:
            ids.add(key)
    return ids


def primary_division(user: Optional[dict]) -> str:
    ids = spk_user_division_ids(user)
    for preferred in ("direksi", "penjualan", "operasional", "kepala_produksi"):
        if preferred in ids:
            return preferred
    return sorted(ids)[0] if ids else "direksi"


def deadline_state(workflow_status: str, deadline: Optional[str], today: Optional[date] = None) -> Optional[str]:
    """Derived read model. None means no deadline warning (completed/cancelled)."""
    if workflow_status in TERMINAL:
        return None
    if not deadline:
        return "ON_TRACK"
    try:
        due = date.fromisoformat(str(deadline)[:10])
    except ValueError:
        return "ON_TRACK"
    current = today or today_wib()
    delta = (due - current).days
    if delta < 0:
        return "OVERDUE"
    if delta <= DUE_SOON_DAYS:
        return "DUE_SOON"
    return "ON_TRACK"


def milestone_progress_percent(milestone: dict) -> Decimal:
    kind = str((milestone or {}).get("type") or "")
    if kind == "QUANTITY":
        target = _q2(milestone.get("target_value"))
        actual = _q2(milestone.get("actual_value"))
        if target <= 0:
            return Decimal("0.00")
        if actual < 0:
            actual = Decimal("0.00")
        ratio = actual / target
        if ratio > 1:
            ratio = Decimal("1")
        if ratio < 0:
            ratio = Decimal("0")
        return _q2(ratio * Decimal("100"))
    if kind in ("CHECKLIST", "APPROVAL"):
        return Decimal("100.00") if milestone.get("status") == "COMPLETED" else Decimal("0.00")
    return Decimal("0.00")


def overall_progress_percent(milestones: Sequence[dict]) -> Decimal:
    rows = list(milestones or [])
    total_weight = sum((_q2(row.get("weight")) for row in rows), Decimal("0.00"))
    if total_weight <= 0:
        return Decimal("0.00")
    accrued = sum(
        (milestone_progress_percent(row) * _q2(row.get("weight")) for row in rows),
        Decimal("0.00"),
    )
    percent = _q2(accrued / total_weight)
    if percent > WEIGHT_TOTAL:
        return WEIGHT_TOTAL
    if percent < 0:
        return Decimal("0.00")
    return percent


def _has(perms: set, *keys: str) -> bool:
    return any(key in perms for key in keys)


def _is_admin(user: Optional[dict]) -> bool:
    return str((user or {}).get("role") or "") == "admin"


def can_view(user: dict, perms: set, doc: dict) -> bool:
    if not doc:
        return False
    if _is_admin(user) or PERM_MANAGE in perms or PERM_VIEW_ALL in perms:
        return True
    uid = actor_user_id(user)
    divisions = spk_user_division_ids(user)
    if PERM_VIEW_DIVISION in perms and (
        doc.get("target_division") in divisions or doc.get("source_division") in divisions
    ):
        return True
    if uid and (uid in (doc.get("assigned_user_ids") or []) or uid == doc.get("created_by_id")):
        if _has(perms, PERM_VIEW_OWN, PERM_CREATE, PERM_PROGRESS, PERM_ASSIGN, PERM_VERIFY, PERM_CANCEL):
            return True
    return False


def visibility_query(user: dict, perms: set) -> dict:
    if _is_admin(user) or PERM_MANAGE in perms or PERM_VIEW_ALL in perms:
        return {}
    uid = actor_user_id(user)
    clauses: List[dict] = []
    if PERM_VIEW_DIVISION in perms:
        divisions = list(spk_user_division_ids(user))
        if divisions:
            clauses.append({"target_division": {"$in": divisions}})
            clauses.append({"source_division": {"$in": divisions}})
    if uid and _has(perms, PERM_VIEW_OWN, PERM_CREATE, PERM_PROGRESS, PERM_ASSIGN):
        clauses.append({"assigned_user_ids": uid})
        clauses.append({"created_by_id": uid})
    if not clauses:
        raise HTTPException(403, "Anda tidak berhak melihat SPK.")
    return {"$or": clauses}


def _is_assigned(user: dict, doc: dict) -> bool:
    uid = actor_user_id(user)
    return bool(uid and uid in (doc.get("assigned_user_ids") or []))


def can_update_progress(user: dict, perms: set, doc: dict) -> bool:
    """Only the assigned PIC may change milestone actuals. Division heads,
    directors, and admins do not inherit this through a broad permission.
    """
    if not can_view(user, perms, doc):
        return False
    return _is_assigned(user, doc)


def can_acknowledge(user: dict, perms: set, doc: dict) -> bool:
    if not can_view(user, perms, doc):
        return False
    return _is_assigned(user, doc)


def can_return(user: dict, perms: set, doc: dict) -> bool:
    return can_acknowledge(user, perms, doc)


def can_correct(user: dict, perms: set, doc: dict) -> bool:
    """PIC corrects their own entry. Anyone else needs the recorded-correction
    permission (spk.correct or spk.manage). Role admin alone is not a bypass:
    the HTTP layer only succeeds because an admin session carries that
    permission. This is never the normal progress or return endpoint.
    """
    if not can_view(user, perms, doc):
        return False
    if _is_assigned(user, doc):
        return True
    return PERM_CORRECT in perms or PERM_MANAGE in perms


def can_add_note(user: dict, perms: set, doc: dict) -> bool:
    if not can_view(user, perms, doc):
        return False
    if doc.get("workflow_status") == STATUS_DRAFT:
        return False
    if _is_assigned(user, doc):
        return True
    uid = actor_user_id(user)
    if uid and uid == doc.get("created_by_id"):
        return True
    return _is_admin(user) or _has(perms, PERM_COMMENT, PERM_VIEW_DIVISION, PERM_VIEW_ALL, PERM_MANAGE)


def can_edit_content(user: dict, perms: set, doc: dict) -> bool:
    if doc.get("workflow_status") not in (STATUS_DRAFT, STATUS_RETURNED):
        return False
    uid = actor_user_id(user)
    return bool(uid and uid == doc.get("created_by_id")) or _is_admin(user) or PERM_MANAGE in perms


def can_verify(user: dict, perms: set, doc: dict) -> bool:
    if not (_is_admin(user) or PERM_VERIFY in perms or PERM_MANAGE in perms):
        return False
    if not can_view(user, perms, doc):
        return False
    uid = actor_user_id(user)
    if uid and uid in (doc.get("assigned_user_ids") or []) and not (_is_admin(user) or PERM_MANAGE in perms):
        return False
    return True


def _public_pic(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "name": row.get("name") or "",
        "role": row.get("role") or "",
    }


def _public_attachment(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "kind": row.get("kind"),
        "name": row.get("name"),
        "content_type": row.get("content_type"),
        "url": row.get("url"),
        "label": row.get("label"),
        "milestone_id": row.get("milestone_id"),
        "event_id": row.get("event_id"),
        "uploaded_at": row.get("uploaded_at"),
    }


def _public_milestone(row: dict) -> dict:
    progress = milestone_progress_percent(row)
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "description": row.get("description") or "",
        "type": row.get("type"),
        "weight": row.get("weight"),
        "target_value": row.get("target_value"),
        "actual_value": row.get("actual_value"),
        "unit": row.get("unit") or "",
        "status": row.get("status"),
        "order": row.get("order"),
        "requires_evidence": bool(row.get("requires_evidence")),
        "evidence_count": int(row.get("evidence_count") or 0),
        "progress_percent": _dec_str(progress),
        "exceeds_target": str(row.get("type") or "") == "QUANTITY" and _q2(row.get("actual_value")) > _q2(row.get("target_value")) > 0,
        "completed_at": row.get("completed_at"),
        "verified_at": row.get("verified_at"),
        "last_update_at": row.get("last_update_at"),
    }


def _refresh_milestone_status(row: dict, ts: str) -> None:
    progress = milestone_progress_percent(row)
    if progress >= WEIGHT_TOTAL:
        row["status"] = "COMPLETED"
        if not row.get("completed_at"):
            row["completed_at"] = ts
    else:
        actual = _q2(row.get("actual_value"))
        row["status"] = "IN_PROGRESS" if actual > 0 else "PENDING"
        row["completed_at"] = None
        if row.get("type") != "APPROVAL":
            row["verified_at"] = None


def milestones_ready(milestones: Sequence[dict]) -> bool:
    rows = list(milestones or [])
    if not rows:
        return False
    for row in rows:
        if row.get("status") != "COMPLETED":
            return False
        if row.get("requires_evidence") and int(row.get("evidence_count") or 0) < 1:
            return False
    return True


def public_order(doc: Optional[dict], *, today: Optional[date] = None, include_milestones: bool = True) -> Optional[dict]:
    if not doc:
        return None
    current = today or today_wib()
    milestones = list(doc.get("milestones") or [])
    progress = overall_progress_percent(milestones)
    pics = [_public_pic(row) for row in (doc.get("assigned_pics") or [])]
    out = {
        "id": doc.get("id"),
        "spk_number": doc.get("spk_number"),
        "title": doc.get("title"),
        "instruction": doc.get("instruction") or "",
        "priority": doc.get("priority"),
        "source_division": doc.get("source_division"),
        "source_division_label": doc.get("source_division_label") or division_label(doc.get("source_division")),
        "created_by_id": doc.get("created_by_id"),
        "created_by": doc.get("created_by_snapshot") or {},
        "target_division": doc.get("target_division"),
        "target_division_label": doc.get("target_division_label") or division_label(doc.get("target_division")),
        "assigned_pics": pics,
        "assigned_user_ids": list(doc.get("assigned_user_ids") or []),
        "pic_label": ", ".join(p.get("name") for p in pics if p.get("name")),
        "branch_id": doc.get("branch_id") or "",
        "branch_name": doc.get("branch_name") or "",
        "start_date": doc.get("start_date"),
        "deadline": doc.get("deadline"),
        "workflow_status": doc.get("workflow_status"),
        "deadline_state": deadline_state(doc.get("workflow_status"), doc.get("deadline"), current),
        "progress_percent": _dec_str(progress),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "published_at": doc.get("published_at"),
        "completed_at": doc.get("completed_at"),
        "cancelled_at": doc.get("cancelled_at"),
        "cancellation_reason": doc.get("cancellation_reason"),
        "return_reason": doc.get("return_reason"),
        "returned_at": doc.get("returned_at"),
        "returned_by": doc.get("returned_by") or {},
        "revision_reason": doc.get("revision_reason"),
        "revision_at": doc.get("revision_at"),
        "doc_version": int(doc.get("doc_version") or 1),
        "last_activity_at": doc.get("last_activity_at") or doc.get("updated_at"),
        "attachments": [_public_attachment(row) for row in (doc.get("attachments") or [])],
    }
    if include_milestones:
        out["milestones"] = [_public_milestone(row) for row in milestones]
    return out


def public_event(doc: dict) -> dict:
    actor = doc.get("actor") or {}
    return {
        "id": doc.get("id"),
        "spk_id": doc.get("spk_id"),
        "milestone_id": doc.get("milestone_id"),
        "milestone_title": doc.get("milestone_title"),
        "event_type": doc.get("event_type"),
        "actor": actor,
        "actor_name": actor.get("name"),
        "created_at": doc.get("created_at"),
        "note": doc.get("note") or "",
        "previous_actual": doc.get("previous_actual"),
        "new_actual": doc.get("new_actual"),
        "milestone_progress": doc.get("milestone_progress"),
        "overall_before": doc.get("overall_before"),
        "overall_after": doc.get("overall_after"),
        "workflow_status_before": doc.get("workflow_status_before"),
        "workflow_status_after": doc.get("workflow_status_after"),
        "evidence": list(doc.get("evidence") or []),
        "evidence_count": len(doc.get("evidence") or []),
        "reason": doc.get("reason") or "",
        "idempotency_key": doc.get("idempotency_key") or "",
    }


def _normalize_milestones(raw: Any, *, strict: bool) -> List[dict]:
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise HTTPException(400, "Milestone harus berupa daftar.")
    if len(raw) > 20:
        raise HTTPException(400, "Maksimal 20 milestone.")
    rows = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise HTTPException(400, "Milestone tidak valid.")
        title = _clean_text(item.get("title"), max_len=160, field="Judul milestone", required=strict)
        if not title and not strict:
            continue
        kind = str(item.get("type") or "").strip().upper()
        if kind not in MILESTONE_TYPES:
            raise HTTPException(400, "Tipe milestone harus QUANTITY, CHECKLIST, atau APPROVAL.")
        weight = _q2(item.get("weight"))
        if weight <= 0:
            raise HTTPException(400, "Bobot milestone harus lebih besar dari 0.")
        unit = _clean_text(item.get("unit"), max_len=40, field="Unit")
        if kind == "QUANTITY":
            target = _q2(item.get("target_value"))
            if strict and target <= 0:
                raise HTTPException(400, "Target nilai milestone kuantitas harus lebih besar dari 0.")
            if strict and not unit:
                raise HTTPException(400, "Unit milestone kuantitas wajib diisi.")
            actual = Decimal("0.00")
        else:
            target = Decimal("1.00")
            actual = Decimal("0.00")
        rows.append({
            "id": str(item.get("id") or uuid.uuid4()),
            "title": title,
            "description": _clean_text(item.get("description"), max_len=500, field="Deskripsi milestone"),
            "type": kind,
            "weight": _dec_str(weight),
            "target_value": _dec_str(target),
            "actual_value": _dec_str(actual),
            "unit": unit if kind == "QUANTITY" else "",
            "status": "PENDING",
            "order": index,
            "requires_evidence": bool(item.get("requires_evidence")),
            "evidence_count": 0,
            "completed_at": None,
            "verified_at": None,
            "last_update_at": None,
        })
    if strict and not rows:
        raise HTTPException(400, "SPK wajib memiliki minimal satu milestone.")
    if strict:
        total = sum((_q2(row["weight"]) for row in rows), Decimal("0.00"))
        if total != WEIGHT_TOTAL:
            raise HTTPException(400, f"Total bobot milestone harus 100. Saat ini { _dec_str(total) }.")
    return rows


async def ensure_indexes(db) -> None:
    await db[ORDERS].create_index("id", unique=True)
    await db[ORDERS].create_index("spk_number", unique=True)
    await db[ORDERS].create_index("workflow_status")
    await db[ORDERS].create_index("target_division")
    await db[ORDERS].create_index("created_by_id")
    await db[ORDERS].create_index("deadline")
    await db[ORDERS].create_index("assigned_user_ids")
    await db[EVENTS].create_index("spk_id")
    await db[EVENTS].create_index("milestone_id")
    await db[EVENTS].create_index("created_at")
    await db[EVENTS].create_index("idempotency_key", unique=True, sparse=True)
    await db[IDEMPOTENCY].create_index([("actor_id", 1), ("idempotency_key", 1)], unique=True)
    await db[IDEMPOTENCY].create_index("retain_until", expireAfterSeconds=0, name="spk_idempotency_retain_until")


async def _resolve_pics(db, user_ids: Sequence[str]) -> Tuple[List[dict], List[str]]:
    ids: List[str] = []
    for raw in user_ids or []:
        uid = str(raw or "").strip()
        if uid and uid not in ids:
            ids.append(uid)
    if len(ids) > 12:
        raise HTTPException(400, "Maksimal 12 PIC.")
    pics = []
    for uid in ids:
        found = await db.users.find_one(
            {"$or": [{"id": uid}, {"user_id": uid}]},
            {"_id": 0, "password_hash": 0},
        )
        if not found or found.get("status") == "inactive":
            inactive = found if found and found.get("status") == "inactive" else None
            label = str((inactive or {}).get("name") or uid)
            if inactive:
                raise HTTPException(400, f"PIC tidak aktif: {label}.")
            raise HTTPException(400, f"PIC tidak ditemukan: {label}.")
        canonical = str(found.get("id") or found.get("user_id") or uid)
        pics.append({
            "id": canonical,
            "name": str(found.get("name") or found.get("email") or canonical),
            "role": str(found.get("role") or ""),
        })
    return pics, [pic["id"] for pic in pics]


def _event_doc(
    *,
    spk: dict,
    event_type: str,
    actor: dict,
    note: str = "",
    milestone: Optional[dict] = None,
    previous_actual: Optional[str] = None,
    new_actual: Optional[str] = None,
    milestone_progress: Optional[str] = None,
    overall_before: Optional[str] = None,
    overall_after: Optional[str] = None,
    status_before: Optional[str] = None,
    evidence: Optional[List[dict]] = None,
    idempotency_key: Optional[str] = None,
    reason: str = "",
) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "spk_id": spk.get("id"),
        "milestone_id": (milestone or {}).get("id"),
        "milestone_title": (milestone or {}).get("title"),
        "event_type": event_type,
        "actor": actor,
        "created_at": now_iso(),
        "note": note or "",
        "previous_actual": previous_actual,
        "new_actual": new_actual,
        "milestone_progress": milestone_progress,
        "overall_before": overall_before,
        "overall_after": overall_after,
        "workflow_status_before": status_before if status_before is not None else spk.get("workflow_status"),
        "workflow_status_after": spk.get("workflow_status"),
        "evidence": evidence or [],
        "reason": reason or "",
        "idempotency_key": idempotency_key or str(uuid.uuid4()),
    }


async def _insert_event(db, event: dict) -> dict:
    try:
        await db[EVENTS].insert_one(dict(event))
    except DuplicateKeyError:
        existing = await db[EVENTS].find_one({"idempotency_key": event.get("idempotency_key")}, {"_id": 0})
        if existing:
            return existing
        raise
    event.pop("_id", None)
    return event


async def _notify(db, *, user_ids: Sequence[str], event_type: str, dedupe_id: str, spk: dict, title: str, body: str, actor_id: str) -> None:
    link = f"/spk/{spk.get('id')}"
    seen = set()
    for uid in user_ids:
        key = str(uid or "").strip()
        if not key or key == actor_id or key in seen:
            continue
        seen.add(key)
        try:
            await create_notification(
                db,
                user_id=key,
                event_type=event_type,
                approval_id=dedupe_id,
                order_id=str(spk.get("id") or ""),
                po_number=spk.get("spk_number"),
                decision=event_type,
                title=title,
                body=body,
                deep_link=link,
            )
        except Exception:
            continue


def _watchers(spk: dict) -> List[str]:
    ids = list(spk.get("assigned_user_ids") or [])
    creator = str(spk.get("created_by_id") or "")
    if creator:
        ids.append(creator)
    return ids


async def _verifier_ids(db) -> List[str]:
    cursor = db.users.find(
        {"role": {"$in": ["direktur_utama", "head_production", "head_operations", "admin"]}, "status": {"$ne": "inactive"}},
        {"id": 1, "user_id": 1, "_id": 0},
    )
    rows = await cursor.to_list(200)
    out = []
    for row in rows:
        uid = str(row.get("id") or row.get("user_id") or "")
        if uid:
            out.append(uid)
    return out


async def _load(db, spk_id: str) -> Optional[dict]:
    return await db[ORDERS].find_one({"id": spk_id}, {"_id": 0})


async def _require(db, spk_id: str, user: dict, perms: set) -> dict:
    doc = await _load(db, spk_id)
    if not doc or not can_view(user, perms, doc):
        raise HTTPException(404, "SPK tidak ditemukan.")
    return doc


async def _save_version(db, spk_id: str, version: int, updates: dict) -> dict:
    updates = dict(updates)
    updates["updated_at"] = now_iso()
    updates["last_activity_at"] = updates["updated_at"]
    updated = await db[ORDERS].find_one_and_update(
        {"id": spk_id, "doc_version": version},
        {"$set": updates, "$inc": {"doc_version": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        raise HTTPException(409, "SPK berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")
    updated.pop("_id", None)
    return updated


def _apply_header(payload: dict, *, strict: bool, source_division: str) -> dict:
    title = _clean_text(payload.get("title"), max_len=180, field="Judul", required=True)
    instruction = _clean_text(payload.get("instruction") or payload.get("description"), max_len=4000, field="Instruksi", required=strict)
    priority = str(payload.get("priority") or "NORMAL").strip().upper()
    if priority not in PRIORITIES:
        raise HTTPException(400, "Prioritas harus LOW, NORMAL, HIGH, atau URGENT.")
    target = str(payload.get("target_division") or "").strip()
    if strict and target not in SPK_DIVISION_IDS:
        raise HTTPException(400, "Divisi tujuan wajib dipilih.")
    if target and target not in SPK_DIVISION_IDS:
        raise HTTPException(400, "Divisi tujuan tidak dikenal.")
    requested_source = str(payload.get("source_division") or "").strip()
    source = requested_source if requested_source in SPK_DIVISION_IDS else source_division
    start = _parse_date(payload.get("start_date"), "Tanggal mulai", required=strict)
    deadline = _parse_date(payload.get("deadline"), "Deadline", required=strict)
    if start and deadline and deadline < start:
        raise HTTPException(400, "Deadline tidak boleh lebih awal dari tanggal mulai.")
    return {
        "title": title,
        "instruction": instruction,
        "priority": priority,
        "source_division": source or source_division,
        "source_division_label": division_label(source or source_division),
        "target_division": target,
        "target_division_label": division_label(target) if target else "",
        "start_date": start,
        "deadline": deadline,
    }


async def next_spk_number(db) -> str:
    """Monthly sequence SPK-YYYYMM-####. Unique index on spk_number is authoritative."""
    month = today_wib().strftime("%Y%m")
    stem = f"{PREFIX}-{month}-"
    regex = f"^{re.escape(stem)}\\d{{4}}$"
    cursor = db[ORDERS].find({"spk_number": {"$regex": regex}}, {"spk_number": 1, "_id": 0})
    existing = await cursor.to_list(10000)
    max_seq = 0
    for doc in existing:
        matched = _SPK_NUMBER.fullmatch(str(doc.get("spk_number") or ""))
        if not matched or matched.group(1) != month:
            continue
        seq = int(matched.group(2))
        if seq > max_seq:
            max_seq = seq
    if max_seq >= 9999:
        raise HTTPException(409, "Nomor SPK bulan ini sudah penuh.")
    return f"{stem}{max_seq + 1:04d}"


def _retain_until() -> datetime:
    return datetime.now(timezone.utc) + timedelta(days=IDEMPOTENCY_RETENTION_DAYS)


def _payload_hash(payload: dict) -> str:
    body = {key: value for key, value in (payload or {}).items() if key != "idempotency_key"}
    raw = json.dumps(body, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def _idempotent(db, user: dict, payload: dict, action: str, runner):
    """One stored result per user + key + payload. A reused key with a different
    payload is a conflict. Failures release the key so the same attempt can retry.
    """
    await ensure_indexes(db)
    key = str((payload or {}).get("idempotency_key") or "").strip()
    if not key:
        raise HTTPException(400, "Idempotency-Key wajib untuk aksi ini.")
    if len(key) > 200:
        raise HTTPException(400, "Idempotency-Key terlalu panjang.")
    uid = actor_user_id(user)
    digest = _payload_hash(payload)
    lock = get_named_lock(f"spk-idem:{uid}:{key}")
    async with lock:
        existing = await db[IDEMPOTENCY].find_one({"actor_id": uid, "idempotency_key": key}, {"_id": 0})
        if existing:
            if existing.get("payload_hash") != digest:
                raise HTTPException(409, "Idempotency key sudah digunakan untuk data yang berbeda.")
            if existing.get("status") == "done" and existing.get("spk_id"):
                order = await _load(db, existing["spk_id"])
                if order:
                    return public_order(order)
            if existing.get("status") == "pending":
                raise HTTPException(409, "Permintaan yang sama sedang diproses. Ulangi sebentar lagi.")
            await db[IDEMPOTENCY].delete_one({"actor_id": uid, "idempotency_key": key})
        try:
            await db[IDEMPOTENCY].insert_one({
                "id": str(uuid.uuid4()),
                "actor_id": uid,
                "idempotency_key": key,
                "action": action,
                "payload_hash": digest,
                "status": "pending",
                "spk_id": "",
                "created_at": now_iso(),
                "retain_until": _retain_until(),
            })
        except DuplicateKeyError:
            existing = await db[IDEMPOTENCY].find_one({"actor_id": uid, "idempotency_key": key}, {"_id": 0})
            if existing and existing.get("payload_hash") != digest:
                raise HTTPException(409, "Idempotency key sudah digunakan untuk data yang berbeda.")
            if existing and existing.get("status") == "done" and existing.get("spk_id"):
                order = await _load(db, existing["spk_id"])
                if order:
                    return public_order(order)
            raise HTTPException(409, "Permintaan yang sama sedang diproses. Ulangi sebentar lagi.")
        try:
            result = await runner()
        except Exception:
            await db[IDEMPOTENCY].delete_one({"actor_id": uid, "idempotency_key": key, "status": "pending"})
            raise
        await db[IDEMPOTENCY].update_one(
            {"actor_id": uid, "idempotency_key": key},
            {"$set": {"status": "done", "spk_id": result.get("id") or "", "finished_at": now_iso()}},
        )
        return result


async def _effective_perms(db, person: dict) -> set:
    from permissions import ALL_PERMISSIONS, effective_permissions
    if str((person or {}).get("role") or "") == "admin" or (person or {}).get("is_super_admin"):
        return set(ALL_PERMISSIONS)
    docs = await db.role_permissions.find({}, {"_id": 0, "role": 1, "permissions": 1}).to_list(80)
    override = {doc.get("role"): list(doc.get("permissions") or []) for doc in docs if doc.get("role")}
    return effective_permissions((person or {}).get("role"), (person or {}).get("permissions_override"), override or None)


def _pic_can_receive(perms: set, person: dict) -> bool:
    if str((person or {}).get("role") or "") == "admin" or (person or {}).get("is_super_admin"):
        return True
    return bool(set(perms or ()) & PIC_ELIGIBLE_PERMS)


async def _assert_pics_eligible(db, pics: Sequence[dict]) -> None:
    blocked = []
    for pic in pics or []:
        found = await db.users.find_one(
            {"$or": [{"id": pic.get("id")}, {"user_id": pic.get("id")}]},
            {"_id": 0, "password_hash": 0},
        )
        if not found or found.get("status") == "inactive":
            blocked.append(str(pic.get("name") or pic.get("id") or "PIC"))
            continue
        if not _pic_can_receive(await _effective_perms(db, found), found):
            blocked.append(str(pic.get("name") or found.get("name") or pic.get("id")))
    if blocked:
        names = ", ".join(blocked)
        raise HTTPException(
            400,
            f"PIC tidak dapat menerima tugas SPK: {names}. Pengguna harus aktif dan memiliki akses melihat atau mengerjakan SPK.",
        )


async def create_spk(db, payload: dict, user: dict) -> dict:
    async def _run():
        return await _create_spk_once(db, payload, user)
    return await _idempotent(db, user, payload or {}, "create", _run)


async def _create_spk_once(db, payload: dict, user: dict) -> dict:
    publish = bool((payload or {}).get("publish"))
    async with get_named_lock("spk-number"):
        actor = actor_snapshot(user)
        uid = actor["id"]
        if not uid:
            raise HTTPException(400, "Identitas pengguna tidak valid.")
        branch = await load_actor_branch(db, user)
        source = primary_division(user)
        header = _apply_header(payload or {}, strict=publish, source_division=source)
        milestones = _normalize_milestones((payload or {}).get("milestones"), strict=publish)
        pics, pic_ids = await _resolve_pics(db, (payload or {}).get("assigned_user_ids") or [])
        if publish and not pic_ids:
            raise HTTPException(400, "PIC wajib dipilih sebelum SPK diterbitkan.")
        if publish:
            await _assert_pics_eligible(db, pics)
        ts = now_iso()
        doc = {
            "id": str(uuid.uuid4()),
            **header,
            "created_by_id": uid,
            "created_by_snapshot": actor,
            "assigned_pics": pics,
            "assigned_user_ids": pic_ids,
            "branch_id": branch.get("id") or "",
            "branch_name": branch.get("name") or "",
            "workflow_status": STATUS_DRAFT,
            "progress_percent": "0.00",
            "milestones": milestones,
            "attachments": [],
            "created_at": ts,
            "updated_at": ts,
            "published_at": None,
            "completed_at": None,
            "cancelled_at": None,
            "cancellation_reason": None,
            "revision_reason": None,
            "revision_at": None,
            "revision_by": None,
            "verification_requested_at": None,
            "verification_requested_by_id": None,
            "doc_version": 1,
            "last_activity_at": ts,
        }
        if publish:
            doc["workflow_status"] = STATUS_PUBLISHED
            doc["published_at"] = ts
            doc["progress_percent"] = _dec_str(overall_progress_percent(milestones))

        async def _allocate():
            return await next_spk_number(db)

        saved = await insert_with_unique_number(db, ORDERS, "spk_number", doc, _allocate)
        event_type = EVENT_PUBLISH if publish else EVENT_CREATE
        await _insert_event(db, _event_doc(
            spk=saved,
            event_type=event_type,
            actor=actor,
            note=saved.get("title") or "",
            overall_before="0.00",
            overall_after=saved.get("progress_percent"),
            status_before=STATUS_DRAFT if publish else None,
            idempotency_key=str((payload or {}).get("idempotency_key") or "") or None,
        ))
        if publish:
            await _notify(
                db,
                user_ids=saved.get("assigned_user_ids") or [],
                event_type="spk_assigned",
                dedupe_id=f"{saved['id']}:publish",
                spk=saved,
                title="SPK ditugaskan",
                body=f"{saved.get('spk_number')} — {saved.get('title')} ditugaskan kepada Anda.",
                actor_id=uid,
            )
        return public_order(saved)


async def update_draft(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if not can_edit_content(user, perms, doc):
        if doc.get("workflow_status") not in (STATUS_DRAFT, STATUS_RETURNED):
            raise HTTPException(400, "Hanya draft atau SPK yang dikembalikan yang dapat diubah.")
        raise HTTPException(403, "Hanya pembuat yang dapat mengubah SPK ini.")
    version = _version((payload or {}).get("doc_version") if (payload or {}).get("doc_version") is not None else doc.get("doc_version"))
    header = _apply_header({**doc, **(payload or {})}, strict=False, source_division=doc.get("source_division") or primary_division(user))
    if "milestones" in (payload or {}):
        milestones = _normalize_milestones(payload.get("milestones"), strict=False)
    else:
        milestones = list(doc.get("milestones") or [])
    if "assigned_user_ids" in (payload or {}):
        pics, pic_ids = await _resolve_pics(db, payload.get("assigned_user_ids") or [])
    else:
        pics, pic_ids = list(doc.get("assigned_pics") or []), list(doc.get("assigned_user_ids") or [])
    before_pics = list(doc.get("assigned_user_ids") or [])
    updated = await _save_version(db, spk_id, version, {
        **header,
        "assigned_pics": pics,
        "assigned_user_ids": pic_ids,
        "milestones": milestones,
        "progress_percent": _dec_str(overall_progress_percent(milestones)),
    })
    actor = actor_snapshot(user)
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=EVENT_EDIT,
        actor=actor,
        note=updated.get("title") or "",
        status_before=doc.get("workflow_status"),
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
    ))
    if before_pics != pic_ids:
        await _insert_event(db, _event_doc(
            spk=updated,
            event_type=EVENT_PIC_CHANGE,
            actor=actor,
            note="PIC diperbarui.",
            status_before=doc.get("workflow_status"),
            overall_before=doc.get("progress_percent"),
            overall_after=updated.get("progress_percent"),
        ))
    return public_order(updated)


async def delete_draft(db, spk_id: str, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if doc.get("workflow_status") != STATUS_DRAFT:
        raise HTTPException(400, "SPK yang sudah diterbitkan tidak dapat dihapus. Batalkan SPK.")
    if not can_edit_content(user, perms, doc):
        raise HTTPException(403, "Hanya pembuat draft yang dapat menghapus SPK ini.")
    progress = await db[EVENTS].count_documents({"spk_id": spk_id, "event_type": EVENT_PROGRESS})
    if progress:
        raise HTTPException(400, "Draft yang sudah memiliki riwayat progress tidak dapat dihapus.")
    await db[ORDERS].delete_one({"id": spk_id, "workflow_status": STATUS_DRAFT})
    await db[EVENTS].delete_many({"spk_id": spk_id})
    return {"ok": True, "id": spk_id}


async def publish_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    async def _run():
        return await _publish_spk_once(db, spk_id, payload, user, perms)
    body = dict(payload or {})
    body["spk_id"] = spk_id
    return await _idempotent(db, user, body, "publish", _run)


async def _publish_spk_once(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if doc.get("workflow_status") not in (STATUS_DRAFT, STATUS_RETURNED):
        raise HTTPException(400, "Hanya draft atau SPK yang dikembalikan yang dapat diterbitkan.")
    if not can_edit_content(user, perms, doc):
        raise HTTPException(403, "Anda tidak berhak menerbitkan SPK ini.")
    version = _version((payload or {}).get("doc_version", doc.get("doc_version")))
    merged = {**doc, **(payload or {})}
    header = _apply_header(merged, strict=True, source_division=doc.get("source_division") or primary_division(user))
    milestones = _normalize_milestones((payload or {}).get("milestones", doc.get("milestones")), strict=True)
    if "assigned_user_ids" in (payload or {}):
        pics, pic_ids = await _resolve_pics(db, payload.get("assigned_user_ids") or [])
    else:
        pics, pic_ids = list(doc.get("assigned_pics") or []), list(doc.get("assigned_user_ids") or [])
    if not pic_ids:
        raise HTTPException(400, "PIC wajib dipilih sebelum SPK diterbitkan.")
    await _assert_pics_eligible(db, pics)
    uid = actor_user_id(user)
    before = doc.get("workflow_status")
    republish = before == STATUS_RETURNED
    updated = await _save_version(db, spk_id, version, {
        **header,
        "assigned_pics": pics,
        "assigned_user_ids": pic_ids,
        "milestones": milestones,
        "workflow_status": STATUS_PUBLISHED,
        "published_at": now_iso(),
        "progress_percent": _dec_str(overall_progress_percent(milestones)),
        "return_reason": None,
        "returned_at": None,
        "returned_by": None,
    })
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=EVENT_REPUBLISH if republish else EVENT_PUBLISH,
        actor=actor_snapshot(user),
        status_before=before,
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
        idempotency_key=str((payload or {}).get("idempotency_key") or "") or None,
    ))
    await _notify(
        db,
        user_ids=updated.get("assigned_user_ids") or [],
        event_type="spk_republished" if republish else "spk_assigned",
        dedupe_id=f"{updated['id']}:{'republish' if republish else 'publish'}:{updated.get('doc_version')}",
        spk=updated,
        title="SPK diterbitkan ulang" if republish else "SPK ditugaskan",
        body=(
            f"{updated.get('spk_number')} — {updated.get('title')} diterbitkan ulang kepada Anda."
            if republish else
            f"{updated.get('spk_number')} — {updated.get('title')} ditugaskan kepada Anda."
        ),
        actor_id=uid,
    )
    return public_order(updated)


async def _transition(db, spk_id: str, payload: dict, user: dict, perms: set, *, allowed_from: set, new_status: str, event_type: str, guard) -> dict:
    """CAS the order first. The audit event is inserted only from that saved document,
    so it cannot claim a transition the CAS did not commit. Notification is not used here.
    """
    doc = await _require(db, spk_id, user, perms)
    if not guard(user, perms, doc):
        raise HTTPException(403, "Anda tidak berhak melakukan aksi ini pada SPK.")
    if doc.get("workflow_status") not in allowed_from:
        raise HTTPException(400, "Status SPK tidak memungkinkan aksi ini.")
    version = _version((payload or {}).get("doc_version", doc.get("doc_version")))
    before = doc.get("workflow_status")
    updates = {"workflow_status": new_status}
    updated = await _save_version(db, spk_id, version, updates)
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=event_type,
        actor=actor_snapshot(user),
        note=_clean_text((payload or {}).get("note"), max_len=1000, field="Catatan"),
        status_before=before,
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
    ))
    return public_order(updated)


def _progress_status_error(status: str) -> HTTPException:
    if status == STATUS_WAITING:
        return HTTPException(400, "Progress tidak dapat diubah sampai verifikator meminta revisi.")
    if status in TERMINAL:
        return HTTPException(400, "Progress tidak dapat diubah.")
    if status in (STATUS_PUBLISHED, STATUS_ACKNOWLEDGED):
        return HTTPException(400, "Mulai kerjakan SPK sebelum memperbarui progress.")
    return HTTPException(400, "Status SPK tidak memungkinkan perubahan progress.")


async def _notify_watchers(db, order: dict, user: dict, *, event_type: str, dedupe_id: str, title: str, body: str) -> None:
    await _notify(
        db,
        user_ids=_watchers(order),
        event_type=event_type,
        dedupe_id=dedupe_id,
        spk=order,
        title=title,
        body=body,
        actor_id=actor_user_id(user),
    )


async def acknowledge_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    order = await _transition(
        db, spk_id, payload, user, perms,
        allowed_from={STATUS_PUBLISHED},
        new_status=STATUS_ACKNOWLEDGED,
        event_type=EVENT_ACKNOWLEDGE,
        guard=can_acknowledge,
    )
    await _notify_watchers(
        db, order, user,
        event_type="spk_acknowledged",
        dedupe_id=f"{order['id']}:ack:{order.get('doc_version')}",
        title="SPK diterima",
        body=f"{order.get('spk_number')} — {order.get('title')} diterima oleh PIC.",
    )
    return order


async def start_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    order = await _transition(
        db, spk_id, payload, user, perms,
        allowed_from={STATUS_ACKNOWLEDGED},
        new_status=STATUS_IN_PROGRESS,
        event_type=EVENT_START,
        guard=can_acknowledge,
    )
    await _notify_watchers(
        db, order, user,
        event_type="spk_started",
        dedupe_id=f"{order['id']}:start:{order.get('doc_version')}",
        title="SPK dimulai",
        body=f"{order.get('spk_number')} — {order.get('title')} mulai dikerjakan.",
    )
    return order


async def return_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if not can_return(user, perms, doc):
        raise HTTPException(403, "Hanya PIC yang ditugaskan yang dapat mengembalikan SPK.")
    status = doc.get("workflow_status")
    if status != STATUS_PUBLISHED:
        if status in {STATUS_ACKNOWLEDGED, STATUS_IN_PROGRESS, STATUS_REVISION, STATUS_WAITING}:
            raise HTTPException(400, "SPK yang sudah dimulai tidak dapat dikembalikan. Gunakan catatan kendala atau alur revisi.")
        raise HTTPException(400, "Hanya SPK terbit yang dapat dikembalikan.")
    reason = _clean_text((payload or {}).get("reason"), max_len=2000, field="Alasan pengembalian", required=True)
    if len(reason) < 5:
        raise HTTPException(400, "Alasan pengembalian minimal 5 karakter.")
    version = _version((payload or {}).get("doc_version", doc.get("doc_version")))
    actor = actor_snapshot(user)
    updated = await _save_version(db, spk_id, version, {
        "workflow_status": STATUS_RETURNED,
        "return_reason": reason,
        "returned_at": now_iso(),
        "returned_by": actor,
    })
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=EVENT_RETURN,
        actor=actor,
        note=reason,
        reason=reason,
        status_before=status,
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
        idempotency_key=str((payload or {}).get("idempotency_key") or "") or None,
    ))
    await _notify_watchers(
        db, updated, user,
        event_type="spk_returned",
        dedupe_id=f"{updated['id']}:return:{updated.get('doc_version')}",
        title="SPK dikembalikan",
        body=f"{updated.get('spk_number')} dikembalikan: {reason}",
    )
    return public_order(updated)


async def add_note(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    """Catatan tidak mengubah nilai aktual. Bukti milestone hanya boleh ditambah PIC."""
    payload = payload or {}
    doc = await _require(db, spk_id, user, perms)
    if not can_add_note(user, perms, doc):
        raise HTTPException(403, "Anda tidak berhak menambah catatan pada SPK ini.")
    status = doc.get("workflow_status")
    if status in TERMINAL:
        raise HTTPException(400, "SPK yang sudah selesai atau dibatalkan tidak dapat diubah.")
    evidence_only = bool(payload.get("evidence_only"))
    note = _clean_text(payload.get("note"), max_len=2000, field="Catatan", required=not evidence_only)
    links = _clean_links(payload.get("links") or payload.get("evidence_links"))
    assigned = _is_assigned(user, doc)
    if (payload.get("file_ids") or links) and not assigned:
        raise HTTPException(403, "Hanya PIC yang dapat menambahkan bukti pada SPK ini.")
    if (payload.get("file_ids") or links) and status not in PROGRESS_STATUSES:
        raise HTTPException(400, "Bukti hanya dapat ditambahkan saat SPK berjalan atau revisi.")
    files = await _take_files(db, spk_id, payload.get("file_ids") or [], user) if assigned else []
    evidence = links + files
    if evidence_only and not evidence:
        raise HTTPException(400, "Bukti wajib dilampirkan.")
    if not evidence_only and not note:
        raise HTTPException(400, "Catatan wajib diisi.")
    milestone_id = str(payload.get("milestone_id") or "").strip()
    milestones = [dict(row) for row in (doc.get("milestones") or [])]
    target = next((row for row in milestones if row.get("id") == milestone_id), None) if milestone_id else None
    if evidence and not target:
        raise HTTPException(400, "Milestone wajib dipilih saat menambahkan bukti.")
    version = _version(payload.get("doc_version", doc.get("doc_version")))
    actor = actor_snapshot(user)
    ts = now_iso()
    previous = _dec_str(_q2((target or {}).get("actual_value"))) if target else None
    if target and evidence:
        target["evidence_count"] = int(target.get("evidence_count") or 0) + len(evidence)
        target["last_update_at"] = ts
    event = _event_doc(
        spk=doc,
        event_type=EVENT_EVIDENCE if evidence_only else EVENT_NOTE,
        actor=actor,
        note=note or "Bukti ditambahkan.",
        milestone=target,
        previous_actual=previous,
        new_actual=previous,
        overall_before=doc.get("progress_percent"),
        overall_after=doc.get("progress_percent"),
        status_before=status,
        evidence=evidence,
        idempotency_key=str(payload.get("idempotency_key") or "") or None,
    )
    attachments = list(doc.get("attachments") or [])
    for item in evidence:
        attachments.append({
            **item,
            "milestone_id": (target or {}).get("id"),
            "event_id": event["id"],
            "uploaded_at": ts,
            "name": item.get("name") or item.get("label") or item.get("url"),
        })
    updates = {}
    if target and evidence:
        updates["milestones"] = milestones
        updates["attachments"] = attachments
    elif not evidence:
        updates["last_activity_at"] = ts
    updated = await _save_version(db, spk_id, version, updates or {"last_activity_at": ts})
    event["workflow_status_after"] = updated.get("workflow_status")
    await _insert_event(db, event)
    if files:
        await _bind_progress_files(db, spk_id, str((target or {}).get("id") or ""), [item["id"] for item in files])
    return public_order(updated)


async def correct_value(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    """Koreksi tercatat. Bukan endpoint progres biasa dan tidak menghapus event lama."""
    payload = payload or {}
    doc = await _require(db, spk_id, user, perms)
    if not can_correct(user, perms, doc):
        raise HTTPException(403, "Anda tidak berhak mengoreksi nilai SPK ini.")
    status = doc.get("workflow_status")
    if status not in PROGRESS_STATUSES:
        raise _progress_status_error(status)
    reason = _clean_text(payload.get("reason"), max_len=2000, field="Alasan koreksi", required=True)
    if len(reason) < 5:
        raise HTTPException(400, "Alasan koreksi minimal 5 karakter.")
    milestone_id = str(payload.get("milestone_id") or "").strip()
    milestones = [dict(row) for row in (doc.get("milestones") or [])]
    target = next((row for row in milestones if row.get("id") == milestone_id), None)
    if not target:
        raise HTTPException(400, "Milestone tidak ditemukan.")
    if target.get("type") != "QUANTITY":
        raise HTTPException(400, "Koreksi nilai hanya untuk milestone kuantitas.")
    if payload.get("new_actual") is None or payload.get("new_actual") == "":
        raise HTTPException(400, "Nilai aktual baru wajib diisi.")
    new_actual = _q2(payload.get("new_actual"))
    if new_actual < 0:
        raise HTTPException(400, "Nilai aktual tidak boleh negatif.")
    previous = _q2(target.get("actual_value"))
    if new_actual == previous:
        raise HTTPException(400, "Nilai tidak berubah.")
    version = _version(payload.get("doc_version", doc.get("doc_version")))
    ts = now_iso()
    target["actual_value"] = _dec_str(new_actual)
    target["last_update_at"] = ts
    _refresh_milestone_status(target, ts)
    before_overall = _dec_str(overall_progress_percent(doc.get("milestones") or []))
    after_overall = _dec_str(overall_progress_percent(milestones))
    actor = actor_snapshot(user)
    updated = await _save_version(db, spk_id, version, {
        "milestones": milestones,
        "progress_percent": after_overall,
    })
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=EVENT_CORRECTION,
        actor=actor,
        note=reason,
        reason=reason,
        milestone=target,
        previous_actual=_dec_str(previous),
        new_actual=_dec_str(new_actual),
        milestone_progress=_dec_str(milestone_progress_percent(target)),
        overall_before=before_overall,
        overall_after=after_overall,
        status_before=status,
        idempotency_key=str(payload.get("idempotency_key") or "") or None,
    ))
    return public_order(updated)


def _clean_links(raw: Any) -> List[dict]:
    if not raw:
        return []
    if not isinstance(raw, list):
        raise HTTPException(400, "Tautan bukti tidak valid.")
    links = []
    for item in raw[:8]:
        if isinstance(item, str):
            url = item
            label = item
        elif isinstance(item, dict):
            url = item.get("url")
            label = item.get("label") or item.get("url")
        else:
            continue
        text = str(url or "").strip()
        if not text:
            continue
        if len(text) > 500:
            raise HTTPException(400, "Tautan terlalu panjang.")
        if not (text.startswith("https://") or text.startswith("http://")):
            raise HTTPException(400, "Tautan harus diawali http:// atau https://.")
        links.append({"kind": "link", "url": text, "label": _clean_text(label, max_len=160, field="Label tautan") or text})
    return links


async def _take_files(db, spk_id: str, file_ids: Sequence[str], user: dict) -> List[dict]:
    evidence = []
    uid = actor_user_id(user)
    for raw in file_ids or []:
        fid = str(raw or "").strip()
        if not fid:
            continue
        rec = await db.files.find_one({"id": fid, "kind": FILES_KIND, "is_deleted": {"$ne": True}}, {"_id": 0})
        if not rec:
            raise HTTPException(400, "Lampiran tidak ditemukan.")
        owner = str(rec.get("uploaded_by_id") or "")
        if owner and owner != uid and not _is_admin(user):
            raise HTTPException(403, "Lampiran ini bukan milik Anda.")
        bound = str(rec.get("spk_id") or "")
        if bound and bound != spk_id:
            raise HTTPException(400, "Lampiran sudah terikat pada SPK lain.")
        evidence.append({
            "kind": "file",
            "id": fid,
            "name": rec.get("original_filename") or "lampiran",
            "content_type": rec.get("content_type") or "",
        })
    return evidence


def _canonical_event(doc: dict) -> bool:
    return doc.get("commit_state") not in (COMMIT_RESERVED, COMMIT_REJECTED)


async def _reject_progress(db, event: dict, status: int, detail: str, *, clear_evidence: bool = False) -> None:
    updates = {"commit_state": COMMIT_REJECTED, "failure_status": int(status), "failure_detail": detail}
    if clear_evidence:
        updates["evidence"] = []
    await db[EVENTS].update_one(
        {"id": event.get("id"), "commit_state": COMMIT_RESERVED},
        {"$set": updates},
    )
    raise HTTPException(status, detail)


def _progress_failure(event: dict) -> HTTPException:
    status = int(event.get("failure_status") or 409)
    detail = str(event.get("failure_detail") or "SPK berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")
    return HTTPException(status, detail)


async def _notify_progress(db, spk: dict, event: dict, actor_id: str) -> None:
    previous = event.get("previous_actual")
    new_actual = event.get("new_actual")
    await _notify(
        db,
        user_ids=_watchers(spk),
        event_type="spk_progress_updated",
        dedupe_id=str(event.get("id") or ""),
        spk=spk,
        title="Progress SPK diperbarui",
        body=(
            f"{spk.get('spk_number')}: {event.get('milestone_title')} "
            f"{previous} → {new_actual}. "
            f"Progress {event.get('overall_before')}% → {event.get('overall_after')}%."
        ),
        actor_id=actor_id,
    )


async def _finalize_progress_event(db, event: dict, order: dict) -> dict:
    milestone_id = event.get("milestone_id")
    milestone = next((row for row in (order.get("milestones") or []) if row.get("id") == milestone_id), None)
    overall = _dec_str(_q2(order.get("progress_percent")))
    progress = _dec_str(milestone_progress_percent(milestone or {}))
    updated = await db[EVENTS].find_one_and_update(
        {"id": event.get("id"), "commit_state": COMMIT_RESERVED},
        {"$set": {
            "commit_state": COMMIT_APPLIED,
            "overall_after": overall,
            "milestone_progress": progress,
            "workflow_status_after": order.get("workflow_status"),
            "finalized_at": now_iso(),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        updated = await db[EVENTS].find_one({"id": event.get("id")}, {"_id": 0})
    if not updated:
        raise HTTPException(500, "Event progress SPK tidak ditemukan.")
    updated.pop("_id", None)
    return updated


async def _confirm_progress_files(db, file_ids: Sequence[str]) -> None:
    for fid in file_ids or []:
        rec = await db.files.find_one({"id": fid, "kind": FILES_KIND, "is_deleted": {"$ne": True}}, {"_id": 0, "id": 1})
        if not rec:
            raise HTTPException(400, "Lampiran tidak ditemukan.")


async def _bind_progress_files(db, spk_id: str, milestone_id: str, file_ids: Sequence[str]) -> None:
    for fid in file_ids or []:
        await db.files.update_one(
            {"id": fid, "kind": FILES_KIND},
            {"$set": {"spk_id": spk_id, "milestone_id": milestone_id}},
        )


async def _apply_reserved_progress(db, event: dict, order: dict) -> dict:
    """Apply a reserved command at most once, then finalize the canonical event."""
    key = str(event.get("idempotency_key") or "")
    intent = event.get("intent") or {}
    expected = int(intent.get("expected_doc_version") or 0)
    commit = order.get("progress_commit") or {}
    just_applied = False
    if commit.get("idempotency_key") == key and commit.get("event_id") == event.get("id"):
        updated = order
    elif int(order.get("doc_version") or 0) != expected:
        await _reject_progress(db, event, 409, "SPK berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")
    else:
        try:
            await _confirm_progress_files(db, intent.get("file_ids") or [])
        except HTTPException as exc:
            await _reject_progress(db, event, exc.status_code, str(exc.detail), clear_evidence=True)
        try:
            updated = await _save_version(db, order["id"], expected, {
                "milestones": intent.get("milestones") or [],
                "progress_percent": intent.get("progress_percent"),
                "attachments": intent.get("attachments") or [],
                "progress_commit": {"idempotency_key": key, "event_id": event.get("id")},
            })
            just_applied = True
        except HTTPException as exc:
            if exc.status_code != 409:
                raise
            fresh = await _load(db, order["id"])
            fresh_commit = (fresh or {}).get("progress_commit") or {}
            if fresh and fresh_commit.get("idempotency_key") == key and fresh_commit.get("event_id") == event.get("id"):
                updated = fresh
            else:
                await _reject_progress(db, event, 409, "SPK berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")
    if just_applied:
        _crash_progress("mutate")
    await _bind_progress_files(db, order["id"], str(event.get("milestone_id") or ""), intent.get("file_ids") or [])
    finalized = await _finalize_progress_event(db, event, updated)
    return {"order": updated, "event": finalized, "replayed": not just_applied}


async def _finish_progress(db, event: dict, user: dict, perms: set) -> dict:
    state = event.get("commit_state")
    if state == COMMIT_REJECTED:
        raise _progress_failure(event)
    if state == COMMIT_APPLIED:
        current = await _load(db, event.get("spk_id"))
        if not current:
            raise HTTPException(404, "SPK tidak ditemukan.")
        await _notify_progress(db, current, event, actor_user_id(user))
        return {"order": public_order(current), "event": public_event(event), "replayed": True}
    doc = await _require(db, event.get("spk_id"), user, perms)
    if not can_update_progress(user, perms, doc):
        raise HTTPException(403, "Anda tidak berhak memperbarui progress SPK ini.")
    outcome = await _apply_reserved_progress(db, event, doc)
    _crash_progress("canonical")
    stored = outcome["event"]
    await _notify_progress(db, outcome["order"], stored, actor_user_id(user))
    return {
        "order": public_order(outcome["order"]),
        "event": public_event(stored),
        "replayed": outcome["replayed"],
    }


async def record_progress(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    payload = payload or {}
    idem = str(payload.get("idempotency_key") or "").strip() or None
    lock = get_named_lock(f"spk:{spk_id}")
    async with lock:
        if idem:
            existing = await db[EVENTS].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                if existing.get("spk_id") != spk_id:
                    raise HTTPException(409, "Idempotency key sudah digunakan.")
                return await _finish_progress(db, existing, user, perms)
        doc = await _require(db, spk_id, user, perms)
        if not can_update_progress(user, perms, doc):
            raise HTTPException(403, "Anda tidak berhak memperbarui progress SPK ini.")
        if doc.get("workflow_status") not in PROGRESS_STATUSES:
            raise _progress_status_error(doc.get("workflow_status"))
        version = _version(payload.get("doc_version", doc.get("doc_version")))
        if version != int(doc.get("doc_version") or 0):
            raise HTTPException(409, "SPK berubah karena pembaruan bersamaan. Muat ulang lalu coba lagi.")
        milestone_id = str(payload.get("milestone_id") or "").strip()
        milestones = [dict(row) for row in (doc.get("milestones") or [])]
        target = next((row for row in milestones if row.get("id") == milestone_id), None)
        if not target:
            raise HTTPException(400, "Milestone tidak ditemukan.")
        note = _clean_text(payload.get("note"), max_len=2000, field="Catatan")
        links = _clean_links(payload.get("links") or payload.get("evidence_links"))
        files = await _take_files(db, spk_id, payload.get("file_ids") or [], user)
        new_evidence = links + files
        previous = _dec_str(_q2(target.get("actual_value")))
        kind = target.get("type")
        completing = False
        if kind == "QUANTITY":
            if payload.get("new_actual") is None or payload.get("new_actual") == "":
                raise HTTPException(400, "Nilai aktual baru wajib diisi.")
            new_actual = _q2(payload.get("new_actual"))
            if new_actual < 0:
                raise HTTPException(400, "Nilai aktual tidak boleh negatif.")
            previous_dec = _q2(previous)
            if new_actual < previous_dec:
                raise HTTPException(400, "Penurunan nilai tidak dapat dilakukan lewat update progres. Gunakan Koreksi Nilai.")
            if new_actual == 0 and not note:
                raise HTTPException(400, "Update progres bernilai 0 tanpa catatan ditolak.")
            if new_actual == previous_dec:
                raise HTTPException(400, "Nilai tidak berubah. Gunakan Tambah Catatan.")
            target["actual_value"] = _dec_str(new_actual)
            completing = new_actual >= _q2(target.get("target_value")) and _q2(target.get("target_value")) > 0
        elif kind == "CHECKLIST":
            if not payload.get("complete"):
                raise HTTPException(400, "Centang selesai untuk milestone checklist.")
            target["actual_value"] = "1.00"
            target["status"] = "COMPLETED"
            completing = True
            new_actual = Decimal("1.00")
        elif kind == "APPROVAL":
            if not payload.get("approve"):
                raise HTTPException(400, "Setujui milestone approval untuk menyelesaikan target.")
            target["actual_value"] = "1.00"
            target["status"] = "COMPLETED"
            target["verified_at"] = now_iso()
            completing = True
            new_actual = Decimal("1.00")
        else:
            raise HTTPException(400, "Tipe milestone tidak dikenal.")
        already = int(target.get("evidence_count") or 0)
        if completing and target.get("requires_evidence") and already + len(new_evidence) < 1:
            raise HTTPException(400, "Milestone ini mewajibkan bukti sebelum diselesaikan.")
        ts = now_iso()
        if new_evidence:
            target["evidence_count"] = already + len(new_evidence)
        target["last_update_at"] = ts
        _refresh_milestone_status(target, ts)
        if kind == "APPROVAL" and target.get("status") == "COMPLETED" and not target.get("verified_at"):
            target["verified_at"] = ts
        before_overall = _dec_str(overall_progress_percent(doc.get("milestones") or []))
        after_overall = _dec_str(overall_progress_percent(milestones))
        m_progress = _dec_str(milestone_progress_percent(target))
        actor = actor_snapshot(user)
        idem = idem or str(uuid.uuid4())
        event = _event_doc(
            spk=doc,
            event_type=EVENT_PROGRESS,
            actor=actor,
            note=note,
            milestone=target,
            previous_actual=previous,
            new_actual=_dec_str(new_actual),
            milestone_progress=m_progress,
            overall_before=before_overall,
            overall_after=after_overall,
            evidence=new_evidence,
            idempotency_key=idem,
        )
        attachments = list(doc.get("attachments") or [])
        for item in new_evidence:
            attachments.append({
                **item,
                "milestone_id": target.get("id"),
                "event_id": event["id"],
                "uploaded_at": ts,
                "name": item.get("name") or item.get("label") or item.get("url"),
            })
        event["commit_state"] = COMMIT_RESERVED
        event["intent"] = {
            "expected_doc_version": version,
            "milestones": milestones,
            "progress_percent": after_overall,
            "attachments": attachments,
            "file_ids": [item["id"] for item in files],
        }
        stored = await _insert_event(db, event)
        _crash_progress("reserve")
        return await _finish_progress(db, stored, user, perms)


async def request_verification(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if not can_update_progress(user, perms, doc):
        raise HTTPException(403, "Anda tidak berhak mengajukan verifikasi.")
    if doc.get("workflow_status") not in PROGRESS_STATUSES:
        raise HTTPException(400, "SPK belum dapat diajukan untuk verifikasi.")
    if not milestones_ready(doc.get("milestones") or []):
        raise HTTPException(400, "Semua milestone wajib selesai dan bukti wajib terpenuhi sebelum verifikasi.")
    version = _version((payload or {}).get("doc_version", doc.get("doc_version")))
    uid = actor_user_id(user)
    before = doc.get("workflow_status")
    note = _clean_text((payload or {}).get("note"), max_len=1000, field="Catatan")
    updated = await _save_version(db, spk_id, version, {
        "workflow_status": STATUS_WAITING,
        "verification_requested_at": now_iso(),
        "verification_requested_by_id": uid,
    })
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=EVENT_VERIFY_REQUEST,
        actor=actor_snapshot(user),
        note=note,
        status_before=before,
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
    ))
    recipients = _watchers(updated) + await _verifier_ids(db)
    await _notify(
        db,
        user_ids=recipients,
        event_type="spk_verification_requested",
        dedupe_id=f"{updated['id']}:{updated.get('doc_version')}:verify",
        spk=updated,
        title="SPK menunggu verifikasi",
        body=f"{updated.get('spk_number')} — {updated.get('title')} diajukan untuk verifikasi.",
        actor_id=uid,
    )
    return public_order(updated)


async def decide_verification(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if not can_verify(user, perms, doc):
        raise HTTPException(403, "Anda tidak berhak memverifikasi SPK ini.")
    if doc.get("workflow_status") != STATUS_WAITING:
        raise HTTPException(400, "SPK tidak sedang menunggu verifikasi.")
    decision = str((payload or {}).get("decision") or "").strip().lower()
    version = _version((payload or {}).get("doc_version", doc.get("doc_version")))
    actor = actor_snapshot(user)
    before = doc.get("workflow_status")
    if decision in ("approve", "terima", "approved"):
        milestones = [dict(row) for row in (doc.get("milestones") or [])]
        ts = now_iso()
        for row in milestones:
            if row.get("status") == "COMPLETED" and not row.get("verified_at"):
                row["verified_at"] = ts
        updated = await _save_version(db, spk_id, version, {
            "workflow_status": STATUS_COMPLETED,
            "completed_at": ts,
            "milestones": milestones,
            "progress_percent": _dec_str(overall_progress_percent(milestones)),
        })
        await _insert_event(db, _event_doc(
            spk=updated,
            event_type=EVENT_VERIFY_APPROVE,
            actor=actor,
            note=_clean_text((payload or {}).get("note"), max_len=1000, field="Catatan"),
            status_before=before,
            overall_before=doc.get("progress_percent"),
            overall_after=updated.get("progress_percent"),
        ))
        await _notify(
            db,
            user_ids=_watchers(updated),
            event_type="spk_completed",
            dedupe_id=f"{updated['id']}:completed",
            spk=updated,
            title="SPK selesai",
            body=f"{updated.get('spk_number')} — {updated.get('title')} diterima dan selesai.",
            actor_id=actor["id"],
        )
        return public_order(updated)
    if decision in ("revision", "revisi"):
        reason = _clean_text((payload or {}).get("reason") or (payload or {}).get("note"), max_len=2000, field="Alasan revisi", required=True)
        if len(reason) < 3:
            raise HTTPException(400, "Alasan revisi wajib diisi.")
        updated = await _save_version(db, spk_id, version, {
            "workflow_status": STATUS_REVISION,
            "revision_reason": reason,
            "revision_at": now_iso(),
            "revision_by": actor,
        })
        await _insert_event(db, _event_doc(
            spk=updated,
            event_type=EVENT_VERIFY_REVISION,
            actor=actor,
            note=reason,
            status_before=before,
            overall_before=doc.get("progress_percent"),
            overall_after=updated.get("progress_percent"),
        ))
        await _notify(
            db,
            user_ids=_watchers(updated),
            event_type="spk_revision_requested",
            dedupe_id=f"{updated['id']}:{updated.get('doc_version')}:revision",
            spk=updated,
            title="SPK diminta revisi",
            body=f"{updated.get('spk_number')}: {reason}",
            actor_id=actor["id"],
        )
        return public_order(updated)
    raise HTTPException(400, "Keputusan verifikasi harus terima atau revisi.")


async def cancel_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    if not (_is_admin(user) or PERM_CANCEL in perms or PERM_MANAGE in perms):
        raise HTTPException(403, "Anda tidak berhak membatalkan SPK.")
    doc = await _require(db, spk_id, user, perms)
    if doc.get("workflow_status") == STATUS_CANCELLED:
        raise HTTPException(400, "SPK sudah dibatalkan.")
    if doc.get("workflow_status") == STATUS_COMPLETED:
        raise HTTPException(400, "SPK selesai tidak dapat dibatalkan.")
    reason = _clean_text((payload or {}).get("reason"), max_len=2000, field="Alasan pembatalan", required=True)
    if len(reason) < 3:
        raise HTTPException(400, "Alasan pembatalan wajib diisi.")
    version = _version((payload or {}).get("doc_version", doc.get("doc_version")))
    before = doc.get("workflow_status")
    updated = await _save_version(db, spk_id, version, {
        "workflow_status": STATUS_CANCELLED,
        "cancelled_at": now_iso(),
        "cancellation_reason": reason,
    })
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=EVENT_CANCEL,
        actor=actor_snapshot(user),
        note=reason,
        reason=reason,
        status_before=before,
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
    ))
    await _notify_watchers(
        db, updated, user,
        event_type="spk_cancelled",
        dedupe_id=f"{updated['id']}:cancel:{updated.get('doc_version')}",
        title="SPK dibatalkan",
        body=f"{updated.get('spk_number')} dibatalkan: {reason}",
    )
    return public_order(updated)


def _summary(rows: Sequence[dict], today: date) -> dict:
    active = due_soon = overdue = waiting = completed_month = 0
    for row in rows:
        status = row.get("workflow_status")
        state = deadline_state(status, row.get("deadline"), today)
        if status in ACTIVE_STATUSES:
            active += 1
        if state == "DUE_SOON":
            due_soon += 1
        if state == "OVERDUE":
            overdue += 1
        if status == STATUS_WAITING:
            waiting += 1
        completed = str(row.get("completed_at") or "")
        if status == STATUS_COMPLETED and completed:
            try:
                done = datetime.fromisoformat(completed.replace("Z", "+00:00")).astimezone(WIB).date()
            except ValueError:
                done = None
            if done and done.year == today.year and done.month == today.month:
                completed_month += 1
    return {
        "active": active,
        "due_soon": due_soon,
        "overdue": overdue,
        "waiting_verification": waiting,
        "completed_this_month": completed_month,
    }


def _month_bounds(today: date) -> Tuple[str, str]:
    start = datetime(today.year, today.month, 1, tzinfo=WIB).astimezone(timezone.utc)
    if today.month == 12:
        nxt = datetime(today.year + 1, 1, 1, tzinfo=WIB)
    else:
        nxt = datetime(today.year, today.month + 1, 1, tzinfo=WIB)
    return start.isoformat(), nxt.astimezone(timezone.utc).isoformat()


def _combine_query(base: dict, clauses: Sequence[dict]) -> dict:
    parts = []
    if base:
        parts.append(base)
    parts.extend(clause for clause in clauses if clause)
    if not parts:
        return {}
    if len(parts) == 1:
        return parts[0]
    return {"$and": parts}


def _tab_clause(tab: str, user: dict, today: date) -> dict:
    key = str(tab or "mine").strip().lower()
    uid = actor_user_id(user)
    open_status = {"workflow_status": {"$nin": list(TERMINAL)}}
    if key in ("", "mine", "saya"):
        return {"$or": [{"assigned_user_ids": uid}, {"created_by_id": uid}]}
    if key in ("division", "divisi"):
        divisions = list(spk_user_division_ids(user))
        return {"$or": [{"target_division": {"$in": divisions}}, {"source_division": {"$in": divisions}}]}
    if key in ("overdue", "terlambat"):
        return {**open_status, "deadline": {"$lt": today.isoformat(), "$nin": [None, ""]}}
    if key in ("verification", "verifikasi"):
        return {"workflow_status": STATUS_WAITING}
    return {}


def _deadline_clause(state: str, today: date) -> dict:
    key = str(state or "").strip().upper()
    if not key:
        return {}
    today_s = today.isoformat()
    soon = (today + timedelta(days=DUE_SOON_DAYS)).isoformat()
    open_status = {"workflow_status": {"$nin": list(TERMINAL)}}
    if key == "OVERDUE":
        return {**open_status, "deadline": {"$lt": today_s, "$nin": [None, ""]}}
    if key == "DUE_SOON":
        return {**open_status, "deadline": {"$gte": today_s, "$lte": soon}}
    if key == "ON_TRACK":
        return {
            **open_status,
            "$or": [
                {"deadline": {"$in": [None, ""]}},
                {"deadline": {"$gt": soon}},
            ],
        }
    return {}


def _completed_month_clause(today: date) -> dict:
    start, end = _month_bounds(today)
    start_z = start.replace("+00:00", "Z")
    end_z = end.replace("+00:00", "Z")
    return {
        "workflow_status": STATUS_COMPLETED,
        "$or": [
            {"completed_at": {"$gte": start, "$lt": end}},
            {"completed_at": {"$gte": start_z, "$lt": end_z}},
        ],
    }


def _list_tabs(user: dict, perms: set) -> List[str]:
    tabs = ["mine", "overdue", "verification"]
    if PERM_VIEW_DIVISION in perms or PERM_VIEW_ALL in perms or PERM_MANAGE in perms or _is_admin(user):
        tabs.insert(1, "division")
    if PERM_VIEW_ALL in perms or PERM_MANAGE in perms or _is_admin(user):
        tabs.insert(2, "all")
    return tabs


def _page_args(page: Any, page_size: Any) -> Tuple[int, int]:
    try:
        current = int(page or 1)
        size = int(page_size or 20)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, "Halaman tidak valid.") from exc
    if current < 1 or size < 1:
        raise HTTPException(400, "Halaman tidak valid.")
    return current, min(size, 100)


async def list_orders(
    db,
    user: dict,
    perms: set,
    *,
    tab: str = "mine",
    q: str = "",
    status: str = "",
    division: str = "",
    pic: str = "",
    priority: str = "",
    deadline: str = "",
    page: Any = 1,
    page_size: Any = 20,
    completed_month: Any = "",
) -> dict:
    """Read model only. Deadline state is derived here and never persisted."""
    if not (_is_admin(user) or _has(perms, *VIEW_PERMS)):
        raise HTTPException(403, "Anda tidak berhak melihat SPK.")
    current_page, size = _page_args(page, page_size)
    today = today_wib()
    visible = visibility_query(user, perms)
    summary_rows = []
    summary_cursor = db[ORDERS].find(visible, {"_id": 0, "workflow_status": 1, "deadline": 1, "completed_at": 1})
    async for row in summary_cursor:
        summary_rows.append(row)
    summary = _summary(summary_rows, today)
    clauses = [_tab_clause(tab, user, today), _deadline_clause(deadline, today)]
    statuses = [item.strip().upper() for item in str(status or "").split(",") if item.strip()]
    if statuses:
        unknown = [item for item in statuses if item not in WORKFLOW_STATUSES]
        if unknown:
            raise HTTPException(400, "Status SPK tidak dikenal.")
        clauses.append({"workflow_status": {"$in": statuses}})
    if str(completed_month or "").strip() in ("1", "true", "True"):
        clauses.append(_completed_month_clause(today))
    division_filter = str(division or "").strip()
    if division_filter:
        clauses.append({"$or": [{"target_division": division_filter}, {"source_division": division_filter}]})
    pic_filter = str(pic or "").strip()
    if pic_filter:
        clauses.append({"assigned_user_ids": pic_filter})
    priority_filter = str(priority or "").strip().upper()
    if priority_filter:
        if priority_filter not in PRIORITIES:
            raise HTTPException(400, "Prioritas tidak dikenal.")
        clauses.append({"priority": priority_filter})
    needle = str(q or "").strip()
    if needle:
        safe = re.escape(needle)
        clauses.append({"$or": [
            {"spk_number": {"$regex": safe, "$options": "i"}},
            {"title": {"$regex": safe, "$options": "i"}},
        ]})
    query = _combine_query(visible, clauses)
    total = await db[ORDERS].count_documents(query)
    cursor = db[ORDERS].find(query, {"_id": 0}).sort("updated_at", -1).skip((current_page - 1) * size).limit(size)
    rows = await cursor.to_list(size)
    return {
        "orders": [public_order(doc, today=today, include_milestones=False) for doc in rows],
        "summary": summary,
        "tabs": _list_tabs(user, perms),
        "page": current_page,
        "page_size": size,
        "total": total,
        "total_pages": (total + size - 1) // size if total else 0,
    }


async def get_order(db, spk_id: str, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    events = await db[EVENTS].find({"spk_id": spk_id}, {"_id": 0}).sort("created_at", 1).to_list(300)
    events = [row for row in events if _canonical_event(row)]
    order = public_order(doc)
    order["events"] = [public_event(row) for row in events]
    status = doc.get("workflow_status")
    editable = can_edit_content(user, perms, doc)
    working = can_update_progress(user, perms, doc) and status in PROGRESS_STATUSES
    order["capabilities"] = {
        "acknowledge": can_acknowledge(user, perms, doc) and status == STATUS_PUBLISHED,
        "start": can_acknowledge(user, perms, doc) and status == STATUS_ACKNOWLEDGED,
        "return_spk": can_return(user, perms, doc) and status == STATUS_PUBLISHED,
        "update_progress": working,
        "add_note": can_add_note(user, perms, doc) and status not in TERMINAL and status != STATUS_DRAFT,
        "add_evidence": working,
        "correct": can_correct(user, perms, doc) and status in PROGRESS_STATUSES,
        "request_verification": working and milestones_ready(doc.get("milestones") or []),
        "verify": can_verify(user, perms, doc) and status == STATUS_WAITING,
        "cancel": (_is_admin(user) or PERM_CANCEL in perms or PERM_MANAGE in perms) and status not in TERMINAL,
        "delete_draft": editable and status == STATUS_DRAFT,
        "edit_draft": editable,
        "publish": editable and status in (STATUS_DRAFT, STATUS_RETURNED),
    }
    return order


async def list_events(db, spk_id: str, user: dict, perms: set) -> List[dict]:
    await _require(db, spk_id, user, perms)
    events = await db[EVENTS].find({"spk_id": spk_id}, {"_id": 0}).sort("created_at", 1).to_list(300)
    return [public_event(row) for row in events if _canonical_event(row)]


async def form_context(db, user: dict) -> dict:
    branch = await load_actor_branch(db, user)
    people = await db.users.find(
        {"status": {"$ne": "inactive"}},
        {"_id": 0, "password_hash": 0},
    ).to_list(400)
    pics = []
    for person in people:
        uid = str(person.get("id") or person.get("user_id") or "")
        if not uid:
            continue
        if not _pic_can_receive(await _effective_perms(db, person), person):
            continue
        pics.append({
            "id": uid,
            "name": person.get("name") or uid,
            "role": person.get("role") or "",
            "divisions": sorted(spk_user_division_ids(person)),
        })
    return {
        "divisions": [{"id": key, "label": label} for key, label in SPK_DIVISIONS],
        "pics": pics,
        "priorities": list(PRIORITIES),
        "milestone_types": list(MILESTONE_TYPES),
        "source_division": primary_division(user),
        "source_division_label": division_label(primary_division(user)),
        "branch_id": branch.get("id") or "",
        "branch_name": branch.get("name") or "",
        "weight_rule": "Total bobot milestone harus tepat 100 pada saat terbit. Progress dihitung dari bobot, bukan persen manual.",
    }


async def filter_metadata(db, user: dict, perms: set) -> dict:
    """Opsi filter daftar. Tidak menyertakan konteks form pembuatan atau email."""
    if not (_is_admin(user) or _has(perms, *VIEW_PERMS)):
        raise HTTPException(403, "Anda tidak berhak melihat SPK.")
    visible = visibility_query(user, perms)
    pics: Dict[str, dict] = {}
    cursor = db[ORDERS].find(visible, {"_id": 0, "assigned_pics": 1})
    async for doc in cursor:
        for pic in doc.get("assigned_pics") or []:
            pid = str(pic.get("id") or "")
            if pid and pid not in pics:
                pics[pid] = {"id": pid, "name": pic.get("name") or pid}
    return {
        "divisions": [{"id": key, "label": label} for key, label in SPK_DIVISIONS],
        "priorities": list(PRIORITIES),
        "statuses": list(WORKFLOW_STATUSES),
        "deadline_states": ["ON_TRACK", "DUE_SOON", "OVERDUE"],
        "pics": list(pics.values()),
    }


async def permission_report(db) -> dict:
    """Read-only. Saved roles are not modified."""
    docs = await db.role_permissions.find({}, {"_id": 0, "role": 1, "label": 1, "permissions": 1}).to_list(200)
    gaps = []
    for doc in docs:
        perms = [str(item) for item in (doc.get("permissions") or [])]
        if any(item.startswith("spk.") for item in perms):
            continue
        gaps.append({"role": doc.get("role"), "label": doc.get("label") or doc.get("role") or ""})
    return {
        "roles_without_spk": gaps,
        "note": "Izin SPK tidak ditambahkan otomatis. Aktifkan secara manual di Role & Hak Akses.",
    }
