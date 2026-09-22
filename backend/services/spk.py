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

WORKFLOW_STATUSES = (
    STATUS_DRAFT,
    STATUS_PUBLISHED,
    STATUS_ACKNOWLEDGED,
    STATUS_IN_PROGRESS,
    STATUS_WAITING,
    STATUS_REVISION,
    STATUS_COMPLETED,
    STATUS_CANCELLED,
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

PERM_CREATE = "spk.create"
PERM_VIEW_OWN = "spk.view_own"
PERM_VIEW_DIVISION = "spk.view_division"
PERM_VIEW_ALL = "spk.view_all"
PERM_ASSIGN = "spk.assign"
PERM_PROGRESS = "spk.update_progress"
PERM_VERIFY = "spk.verify"
PERM_CANCEL = "spk.cancel"
PERM_MANAGE = "spk.manage"
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


def can_update_progress(user: dict, perms: set, doc: dict) -> bool:
    if not can_view(user, perms, doc):
        return False
    if _is_admin(user) or PERM_MANAGE in perms:
        return True
    if PERM_PROGRESS not in perms:
        return False
    uid = actor_user_id(user)
    if uid and uid in (doc.get("assigned_user_ids") or []):
        return True
    if PERM_VIEW_ALL in perms:
        return True
    if PERM_VIEW_DIVISION in perms:
        divisions = spk_user_division_ids(user)
        return doc.get("target_division") in divisions or doc.get("source_division") in divisions
    return False


def can_acknowledge(user: dict, perms: set, doc: dict) -> bool:
    if not can_view(user, perms, doc):
        return False
    uid = actor_user_id(user)
    if uid and uid in (doc.get("assigned_user_ids") or []):
        return True
    if PERM_VIEW_DIVISION in perms and doc.get("target_division") in spk_user_division_ids(user):
        return True
    return False


def can_verify(user: dict, perms: set, doc: dict) -> bool:
    if not (_is_admin(user) or PERM_VERIFY in perms or PERM_MANAGE in perms):
        return False
    if not can_view(user, perms, doc):
        return False
    uid = actor_user_id(user)
    if uid and uid in (doc.get("assigned_user_ids") or []) and not (_is_admin(user) or PERM_MANAGE in perms):
        return False
    return True


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
    pics = list(doc.get("assigned_pics") or [])
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
            {"$or": [{"id": uid}, {"user_id": uid}], "status": {"$ne": "inactive"}},
            {"_id": 0, "password_hash": 0},
        )
        if not found:
            raise HTTPException(400, "PIC tidak ditemukan atau tidak aktif.")
        canonical = str(found.get("id") or found.get("user_id") or uid)
        pics.append({
            "id": canonical,
            "name": str(found.get("name") or found.get("email") or canonical),
            "role": str(found.get("role") or ""),
            "email": str(found.get("email") or ""),
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


async def create_spk(db, payload: dict, user: dict) -> dict:
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
    if doc.get("workflow_status") != STATUS_DRAFT:
        raise HTTPException(400, "Hanya draft yang dapat diubah.")
    uid = actor_user_id(user)
    if doc.get("created_by_id") != uid and not (_is_admin(user) or PERM_MANAGE in perms):
        raise HTTPException(403, "Hanya pembuat draft yang dapat mengubah SPK ini.")
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
    updated = await _save_version(db, spk_id, version, {
        **header,
        "assigned_pics": pics,
        "assigned_user_ids": pic_ids,
        "milestones": milestones,
        "progress_percent": _dec_str(overall_progress_percent(milestones)),
    })
    return public_order(updated)


async def delete_draft(db, spk_id: str, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if doc.get("workflow_status") != STATUS_DRAFT:
        raise HTTPException(400, "SPK yang sudah diterbitkan tidak dapat dihapus. Batalkan SPK.")
    uid = actor_user_id(user)
    if doc.get("created_by_id") != uid and not (_is_admin(user) or PERM_MANAGE in perms):
        raise HTTPException(403, "Hanya pembuat draft yang dapat menghapus SPK ini.")
    progress = await db[EVENTS].count_documents({"spk_id": spk_id, "event_type": EVENT_PROGRESS})
    if progress:
        raise HTTPException(400, "Draft yang sudah memiliki riwayat progress tidak dapat dihapus.")
    await db[ORDERS].delete_one({"id": spk_id, "workflow_status": STATUS_DRAFT})
    await db[EVENTS].delete_many({"spk_id": spk_id})
    return {"ok": True, "id": spk_id}


async def publish_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    if doc.get("workflow_status") != STATUS_DRAFT:
        raise HTTPException(400, "Hanya draft yang dapat diterbitkan.")
    uid = actor_user_id(user)
    if doc.get("created_by_id") != uid and not (_is_admin(user) or PERM_MANAGE in perms or PERM_ASSIGN in perms):
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
    before = doc.get("workflow_status")
    updated = await _save_version(db, spk_id, version, {
        **header,
        "assigned_pics": pics,
        "assigned_user_ids": pic_ids,
        "milestones": milestones,
        "workflow_status": STATUS_PUBLISHED,
        "published_at": now_iso(),
        "progress_percent": _dec_str(overall_progress_percent(milestones)),
    })
    await _insert_event(db, _event_doc(
        spk=updated,
        event_type=EVENT_PUBLISH,
        actor=actor_snapshot(user),
        status_before=before,
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
    ))
    await _notify(
        db,
        user_ids=updated.get("assigned_user_ids") or [],
        event_type="spk_assigned",
        dedupe_id=f"{updated['id']}:publish",
        spk=updated,
        title="SPK ditugaskan",
        body=f"{updated.get('spk_number')} — {updated.get('title')} ditugaskan kepada Anda.",
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


async def acknowledge_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    return await _transition(
        db, spk_id, payload, user, perms,
        allowed_from={STATUS_PUBLISHED},
        new_status=STATUS_ACKNOWLEDGED,
        event_type=EVENT_ACKNOWLEDGE,
        guard=can_acknowledge,
    )


async def start_spk(db, spk_id: str, payload: dict, user: dict, perms: set) -> dict:
    return await _transition(
        db, spk_id, payload, user, perms,
        allowed_from={STATUS_ACKNOWLEDGED},
        new_status=STATUS_IN_PROGRESS,
        event_type=EVENT_START,
        guard=can_acknowledge,
    )


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
            raise HTTPException(400, "Mulai kerjakan SPK sebelum memperbarui progress.")
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
        status_before=before,
        overall_before=doc.get("progress_percent"),
        overall_after=updated.get("progress_percent"),
    ))
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


def _tab_match(doc: dict, tab: str, user: dict, today: date) -> bool:
    key = str(tab or "mine").strip().lower()
    uid = actor_user_id(user)
    if key in ("", "mine", "saya"):
        return uid in (doc.get("assigned_user_ids") or []) or uid == doc.get("created_by_id")
    if key in ("division", "divisi"):
        divisions = spk_user_division_ids(user)
        return doc.get("target_division") in divisions or doc.get("source_division") in divisions
    if key in ("all", "semua"):
        return True
    if key in ("overdue", "terlambat"):
        return deadline_state(doc.get("workflow_status"), doc.get("deadline"), today) == "OVERDUE"
    if key in ("verification", "verifikasi"):
        return doc.get("workflow_status") == STATUS_WAITING
    return True


async def list_orders(db, user: dict, perms: set, *, tab: str = "mine", q: str = "", status: str = "", division: str = "", pic: str = "", priority: str = "", deadline: str = "") -> dict:
    """Read model only. Deadline state is derived here and never persisted."""
    if not (_is_admin(user) or _has(perms, *VIEW_PERMS)):
        raise HTTPException(403, "Anda tidak berhak melihat SPK.")
    query = visibility_query(user, perms)
    rows = await db[ORDERS].find(query, {"_id": 0}).sort("updated_at", -1).to_list(500)
    today = today_wib()
    summary = _summary(rows, today)
    needle = str(q or "").strip().lower()
    status_filter = str(status or "").strip().upper()
    division_filter = str(division or "").strip()
    pic_filter = str(pic or "").strip()
    priority_filter = str(priority or "").strip().upper()
    deadline_filter = str(deadline or "").strip().upper()
    filtered = []
    for doc in rows:
        if not _tab_match(doc, tab, user, today):
            continue
        if status_filter and doc.get("workflow_status") != status_filter:
            continue
        if division_filter and division_filter not in (doc.get("target_division"), doc.get("source_division")):
            continue
        if pic_filter and pic_filter not in (doc.get("assigned_user_ids") or []):
            continue
        if priority_filter and doc.get("priority") != priority_filter:
            continue
        state = deadline_state(doc.get("workflow_status"), doc.get("deadline"), today)
        if deadline_filter and (state or "") != deadline_filter:
            continue
        if needle:
            hay = " ".join([
                str(doc.get("spk_number") or ""),
                str(doc.get("title") or ""),
            ]).lower()
            if needle not in hay:
                continue
        filtered.append(public_order(doc, today=today, include_milestones=False))
    tabs = ["mine", "overdue", "verification"]
    if PERM_VIEW_DIVISION in perms or PERM_VIEW_ALL in perms or PERM_MANAGE in perms or _is_admin(user):
        tabs.insert(1, "division")
    if PERM_VIEW_ALL in perms or PERM_MANAGE in perms or _is_admin(user):
        tabs.insert(2, "all")
    return {"orders": filtered, "summary": summary, "tabs": tabs}


async def get_order(db, spk_id: str, user: dict, perms: set) -> dict:
    doc = await _require(db, spk_id, user, perms)
    events = await db[EVENTS].find({"spk_id": spk_id}, {"_id": 0}).sort("created_at", 1).to_list(300)
    events = [row for row in events if _canonical_event(row)]
    order = public_order(doc)
    order["events"] = [public_event(row) for row in events]
    order["capabilities"] = {
        "acknowledge": can_acknowledge(user, perms, doc) and doc.get("workflow_status") == STATUS_PUBLISHED,
        "start": can_acknowledge(user, perms, doc) and doc.get("workflow_status") == STATUS_ACKNOWLEDGED,
        "update_progress": can_update_progress(user, perms, doc) and doc.get("workflow_status") in PROGRESS_STATUSES,
        "request_verification": can_update_progress(user, perms, doc) and doc.get("workflow_status") in PROGRESS_STATUSES and milestones_ready(doc.get("milestones") or []),
        "verify": can_verify(user, perms, doc) and doc.get("workflow_status") == STATUS_WAITING,
        "cancel": (_is_admin(user) or PERM_CANCEL in perms or PERM_MANAGE in perms) and doc.get("workflow_status") not in TERMINAL,
        "delete_draft": doc.get("workflow_status") == STATUS_DRAFT and (doc.get("created_by_id") == actor_user_id(user) or _is_admin(user) or PERM_MANAGE in perms),
        "edit_draft": doc.get("workflow_status") == STATUS_DRAFT and (doc.get("created_by_id") == actor_user_id(user) or _is_admin(user) or PERM_MANAGE in perms),
    }
    return order


async def list_events(db, spk_id: str, user: dict, perms: set) -> List[dict]:
    await _require(db, spk_id, user, perms)
    events = await db[EVENTS].find({"spk_id": spk_id}, {"_id": 0}).sort("created_at", 1).to_list(300)
    return [public_event(row) for row in events if _canonical_event(row)]


async def form_context(db, user: dict) -> dict:
    branch = await load_actor_branch(db, user)
    people = await db.users.find({"status": {"$ne": "inactive"}}, {"_id": 0, "id": 1, "user_id": 1, "name": 1, "email": 1, "role": 1, "divisions": 1, "division_ids": 1}).to_list(400)
    pics = []
    for person in people:
        uid = str(person.get("id") or person.get("user_id") or "")
        if not uid:
            continue
        pics.append({
            "id": uid,
            "name": person.get("name") or person.get("email") or uid,
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
