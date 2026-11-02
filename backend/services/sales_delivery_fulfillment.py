"""Stage 3+5B: SJ-level one-shot Loading / POD + safe recovery.

Uses canonical sales_deliveries.id, per-PO mirrors, existing stock/permission engines.
Standalone multi-PO one-shot is fail-closed before stock/POD mutations.
Shared proof is stored once on the SJ and referenced by each source confirmation.

Stage 5B:
- Replay never claims success without evidence-based reconcile.
- Stuck fence recovery via conditional lease takeover + evidence.
- Resume pending sources only; no blind reverse; shared proof preserved.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from fastapi import HTTPException

from services.sales_delivery_allocations import (
    assess_multi_source_slice_fulfillment,
    delivery_source_po_ids,
    is_multi_source_delivery,
    mongo_transactions_available,
    normalize_delivery_source_allocations,
    require_operational_sales_delivery,
    slice_lines_for_po,
)

_logger = logging.getLogger(__name__)

FULFILLMENT_FENCE_FIELD = "fulfillment_recovery_fence"
FULFILLMENT_PENDING_FIELD = "fulfillment_commit_pending"
FULFILLMENT_ACTION_FIELD = "fulfillment_action_id"
FULFILLMENT_KIND_FIELD = "fulfillment_kind"
FULFILLMENT_TOKEN_FIELD = "fulfillment_token"
FULFILLMENT_PROGRESS_FIELD = "fulfillment_source_progress"
FULFILLMENT_MANUAL_REVIEW_FIELD = "fulfillment_manual_review_required"
SHARED_POD_PROOF_FIELD = "shared_pod_proof"

OUTCOME_COMPLETE = "complete"
OUTCOME_PARTIAL = "partial"
OUTCOME_IN_PROGRESS = "in_progress"
OUTCOME_RECOVERY_REQUIRED = "recovery_required"
OUTCOME_MANUAL_REVIEW = "manual_review_required"

ONESHOT_BLOCKED_STANDALONE = (
    "One-shot Loading/POD multi-PO tidak tersedia pada Mongo standalone: "
    "atomic cross-PO tidak dijamin. Gunakan konfirmasi per-PO "
    "(fallback operasional), atau jalankan pada replica set."
)
ONESHOT_BLOCKED_PENDING = "Surat Jalan provisional/pending tidak dapat di-Loading/POD."
ONESHOT_BLOCKED_CANCELLED = "Surat Jalan dibatalkan tidak dapat di-Loading/POD."
ONESHOT_MISSING_MIRROR = (
    "One-shot ditolak: mirror shipment tidak lengkap untuk semua source PO. Fail-closed."
)


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def delivery_is_cancelled(delivery: Optional[dict]) -> bool:
    if not isinstance(delivery, dict):
        return True
    if delivery.get("is_cancelled") is True:
        return True
    status = _norm(delivery.get("status")).lower()
    return status in {"cancelled", "dibatalkan", "deleted", "batal"}


async def oneshot_engine_allows_multi(db) -> Tuple[bool, str]:
    """Multi-PO one-shot requires replica-set transactional capability probe."""
    if await mongo_transactions_available(db):
        return True, ""
    return False, ONESHOT_BLOCKED_STANDALONE


async def load_orders_for_delivery(db, delivery: dict) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for pid in delivery_source_po_ids(delivery):
        order = await db.orders.find_one({"id": pid}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"PO sumber '{pid}' tidak ditemukan untuk SJ.")
        out[pid] = order
    return out


def _find_mirror(order: dict, delivery: dict) -> Optional[dict]:
    identity = {
        _norm(delivery.get("id")),
        _norm(delivery.get("sj_number")),
        _norm(delivery.get("number")),
    }
    identity.discard("")
    for sh in order.get("shipments") or []:
        if not isinstance(sh, dict):
            continue
        tokens = {
            _norm(sh.get("id")),
            _norm(sh.get("sales_delivery_id")),
            _norm(sh.get("number")),
            _norm(sh.get("sj_number")),
        }
        if identity & tokens:
            return sh
    return None


def _shared_proof_valid(proof: Optional[dict]) -> bool:
    if not isinstance(proof, dict):
        return False
    if not _norm(proof.get("id")):
        return False
    if not _norm(proof.get("recipient_name")):
        return False
    photos = proof.get("photo_refs") or []
    if not isinstance(photos, list) or len([p for p in photos if isinstance(p, dict) and _norm(p.get("id"))]) < 2:
        return False
    sig = proof.get("signature_ref")
    drv = proof.get("driver_signature_ref")
    if not isinstance(sig, dict) or not _norm(sig.get("id") or sig.get("data_url") or sig.get("storage_path")):
        return False
    if not isinstance(drv, dict) or not _norm(drv.get("id") or drv.get("data_url") or drv.get("storage_path")):
        return False
    return True


def build_source_progress_from_evidence(
    delivery: dict,
    orders: Dict[str, dict],
    *,
    kind: Optional[str] = None,
    action_id: Optional[str] = None,
) -> List[dict]:
    """Progress rows from canonical confirmations — not invented status."""
    slice_info = assess_multi_source_slice_fulfillment(delivery, orders)
    kind_n = _norm(kind) or _norm(delivery.get(FULFILLMENT_KIND_FIELD)) or "loading"
    action = _norm(action_id) or _norm(delivery.get(FULFILLMENT_ACTION_FIELD))
    alloc_qty: Dict[str, int] = {}
    for alloc in normalize_delivery_source_allocations(delivery):
        pid = _norm(alloc.get("source_po_id"))
        if not pid:
            continue
        alloc_qty[pid] = alloc_qty.get(pid, 0) + _as_int(alloc.get("allocated_qty"))
    rows: List[dict] = []
    for src in slice_info.get("sources") or []:
        po_id = _norm(src.get("source_po_id"))
        has_mirror = bool(src.get("has_mirror"))
        # Prefer max(slice, allocation) so empty slice lines cannot fake complete.
        slice_qty = max(_as_int(src.get("slice_qty")), alloc_qty.get(po_id, 0))
        loading_qty = _as_int(src.get("loading_qty"))
        delivered_qty = _as_int(src.get("delivered_qty"))
        loading_ok = slice_qty <= 0 or loading_qty >= slice_qty
        delivered_ok = slice_qty <= 0 or bool(src.get("delivered"))
        if slice_qty <= 0 and po_id in alloc_qty and alloc_qty[po_id] <= 0:
            loading_ok = bool(src.get("loading_complete"))
            delivered_ok = bool(src.get("delivered"))
        elif slice_qty > 0:
            # Recompute completeness from evidence quantities (ignore false slice_qty==0 complete).
            loading_ok = loading_qty >= slice_qty
            delivered_ok = bool(src.get("delivered")) and (
                delivered_qty >= slice_qty or delivered_qty > 0 or bool(src.get("delivered"))
            )
            # For loading completeness require qty; for delivered keep confirmation flag.
            if kind_n != "pod":
                delivered_ok = bool(src.get("delivered"))
            else:
                delivered_ok = bool(src.get("delivered"))
        ambiguous = False
        status = "pending"
        if not has_mirror:
            ambiguous = True
            status = "ambiguous"
        elif kind_n == "pod":
            if delivered_ok:
                status = "complete"
            elif loading_ok:
                status = "pending"
            else:
                status = "ambiguous"
                ambiguous = True
        else:
            if loading_ok and slice_qty > 0:
                status = "complete"
            elif loading_ok and slice_qty <= 0:
                # Declared source without measurable qty → fail-closed ambiguous
                ambiguous = True
                status = "ambiguous"
            else:
                status = "pending"
        rows.append({
            "po_id": po_id,
            "fulfillment_kind": kind_n,
            "child_action_id": f"{action}:{po_id}" if action else "",
            "status": status,
            "ambiguous": ambiguous,
            "loading_complete": loading_ok,
            "delivered": delivered_ok,
            "has_mirror": has_mirror,
            "slice_qty": slice_qty,
            "loading_qty": loading_qty,
            "delivered_qty": delivered_qty,
            "updated_at": _now_iso(),
            "evidence": "order.loading_confirmations|delivery_confirmations|shipments|source_allocations",
        })
    return rows


def classify_recovery_outcome(
    *,
    progress: List[dict],
    fence_active: bool,
    kind: str,
    shared_proof: Optional[dict] = None,
) -> dict:
    """Derive recovery outcome from evidence progress (fail-closed on ambiguity)."""
    kind_n = _norm(kind) or "loading"
    complete_ids = [p["po_id"] for p in progress if p.get("status") == "complete"]
    pending_ids = [p["po_id"] for p in progress if p.get("status") == "pending"]
    ambiguous_ids = [p["po_id"] for p in progress if p.get("status") == "ambiguous" or p.get("ambiguous")]

    if ambiguous_ids:
        return {
            "outcome": OUTCOME_MANUAL_REVIEW,
            "ok": False,
            "complete_sources": complete_ids,
            "pending_sources": pending_ids,
            "ambiguous_sources": ambiguous_ids,
            "resume_available": False,
            "reason": (
                "Source ambigu (mirror hilang / bukti tidak cukup). "
                "Manual review required — tidak otomatis resume atau reverse."
            ),
        }

    if kind_n == "pod" and pending_ids:
        if not _shared_proof_valid(shared_proof):
            return {
                "outcome": OUTCOME_MANUAL_REVIEW,
                "ok": False,
                "complete_sources": complete_ids,
                "pending_sources": pending_ids,
                "ambiguous_sources": [],
                "resume_available": False,
                "reason": "Shared POD proof hilang/rusak atau tidak terverifikasi. Manual review required.",
            }

    if progress and len(complete_ids) == len(progress) and not pending_ids:
        return {
            "outcome": OUTCOME_COMPLETE,
            "ok": True,
            "complete_sources": complete_ids,
            "pending_sources": [],
            "ambiguous_sources": [],
            "resume_available": False,
            "reason": "",
        }

    if fence_active and not complete_ids and pending_ids:
        return {
            "outcome": OUTCOME_IN_PROGRESS if fence_active else OUTCOME_RECOVERY_REQUIRED,
            "ok": False,
            "complete_sources": complete_ids,
            "pending_sources": pending_ids,
            "ambiguous_sources": [],
            "resume_available": False,
            "reason": "Fulfillment masih in_progress / belum ada source yang complete menurut bukti.",
        }

    if complete_ids and pending_ids:
        return {
            "outcome": OUTCOME_RECOVERY_REQUIRED if fence_active else OUTCOME_PARTIAL,
            "ok": False,
            "complete_sources": complete_ids,
            "pending_sources": pending_ids,
            "ambiguous_sources": [],
            "resume_available": True,
            "reason": (
                "Sebagian source selesai menurut bukti. "
                "Resume hanya untuk pending; hasil complete dipertahankan."
            ),
        }

    if fence_active:
        return {
            "outcome": OUTCOME_IN_PROGRESS,
            "ok": False,
            "complete_sources": complete_ids,
            "pending_sources": pending_ids,
            "ambiguous_sources": [],
            "resume_available": False,
            "reason": "Fence aktif; tunggu worker atau jalankan reconcile mutating.",
        }

    if pending_ids and not complete_ids:
        if fence_active:
            return {
                "outcome": OUTCOME_IN_PROGRESS,
                "ok": False,
                "complete_sources": [],
                "pending_sources": pending_ids,
                "ambiguous_sources": [],
                "resume_available": False,
                "reason": "Fence aktif tanpa source complete — reconcile atau tunggu worker.",
            }
        # Never-started / clean pending: not a failure state — fresh oneshot may run.
        return {
            "outcome": "ready",
            "ok": False,
            "complete_sources": [],
            "pending_sources": pending_ids,
            "ambiguous_sources": [],
            "resume_available": False,
            "reason": "",
        }

    return {
        "outcome": OUTCOME_RECOVERY_REQUIRED,
        "ok": False,
        "complete_sources": complete_ids,
        "pending_sources": pending_ids,
        "ambiguous_sources": ambiguous_ids,
        "resume_available": bool(pending_ids) and not ambiguous_ids,
        "reason": "Recovery diperlukan.",
    }


async def inspect_fulfillment_recovery(db, delivery_id: str) -> dict:
    """Read-only reconciliation status — no mutations."""
    delivery = await require_operational_sales_delivery(db, delivery_id, attempt_reconcile=True)
    orders = await load_orders_for_delivery(db, delivery)
    kind = _norm(delivery.get(FULFILLMENT_KIND_FIELD)) or "loading"
    fence_active = (
        delivery.get(FULFILLMENT_PENDING_FIELD) is True
        or delivery.get(FULFILLMENT_FENCE_FIELD) is True
    )
    progress = build_source_progress_from_evidence(
        delivery,
        orders,
        kind=kind,
        action_id=_norm(delivery.get(FULFILLMENT_ACTION_FIELD)),
    )
    shared = delivery.get(SHARED_POD_PROOF_FIELD) if isinstance(delivery.get(SHARED_POD_PROOF_FIELD), dict) else None
    classified = classify_recovery_outcome(
        progress=progress,
        fence_active=fence_active,
        kind=kind,
        shared_proof=shared,
    )
    # Prefer stored manual review flag
    if delivery.get(FULFILLMENT_MANUAL_REVIEW_FIELD) is True:
        classified = {
            **classified,
            "outcome": OUTCOME_MANUAL_REVIEW,
            "ok": False,
            "resume_available": False,
            "reason": classified.get("reason")
            or "Fulfillment ditandai manual_review_required.",
        }
    engine_ok, engine_reason = await oneshot_engine_allows_multi(db)
    multi = is_multi_source_delivery(delivery) or len(orders) > 1
    slice_info = assess_multi_source_slice_fulfillment(delivery, orders)
    resume_ok = bool(classified.get("resume_available")) and not (
        multi and not engine_ok
    )
    if multi and not engine_ok:
        resume_reason = engine_reason
    else:
        resume_reason = classified.get("reason") or ""
    return {
        "sales_delivery_id": _norm(delivery.get("id")),
        "sj_number": _norm(delivery.get("sj_number")),
        "fulfillment_kind": kind or None,
        "fulfillment_action_id": _norm(delivery.get(FULFILLMENT_ACTION_FIELD)) or None,
        "fulfillment_token": _norm(delivery.get(FULFILLMENT_TOKEN_FIELD)) or None,
        "fulfillment_commit_pending": delivery.get(FULFILLMENT_PENDING_FIELD) is True,
        "fulfillment_recovery_fence": delivery.get(FULFILLMENT_FENCE_FIELD) is True,
        "fulfillment_last_status": _norm(delivery.get("fulfillment_last_status")) or None,
        "fulfillment_manual_review_required": delivery.get(FULFILLMENT_MANUAL_REVIEW_FIELD) is True
        or classified["outcome"] == OUTCOME_MANUAL_REVIEW,
        "fence_active": fence_active,
        "status": classified["outcome"],
        "ok": bool(classified.get("ok")),
        "complete_sources": classified.get("complete_sources") or [],
        "pending_sources": classified.get("pending_sources") or [],
        "ambiguous_sources": classified.get("ambiguous_sources") or [],
        "resume_available": resume_ok and classified["outcome"] != OUTCOME_MANUAL_REVIEW,
        "reason": resume_reason if not resume_ok and multi and not engine_ok else classified.get("reason") or "",
        "source_progress": progress,
        "slice_fulfillment": slice_info,
        "shared_pod_proof": {
            "id": _norm((shared or {}).get("id")),
            "recipient_name": _norm((shared or {}).get("recipient_name")),
            "photo_count": len((shared or {}).get("photo_refs") or []),
            "has_signature": bool((shared or {}).get("signature_ref")),
            "valid": _shared_proof_valid(shared),
            "reference": _norm((shared or {}).get("id")),
        }
        if shared
        else None,
        "engine_transactions_available": engine_ok,
        "is_multi_source": multi,
    }


async def _acquire_fulfillment_fence(
    db, delivery_id: str, *, action_id: str, kind: str
) -> dict:
    sid = _norm(delivery_id)
    token = str(uuid.uuid4())
    from pymongo import ReturnDocument

    result = await db.sales_deliveries.find_one_and_update(
        {
            "id": sid,
            FULFILLMENT_PENDING_FIELD: {"$ne": True},
            FULFILLMENT_FENCE_FIELD: {"$ne": True},
            "is_cancelled": {"$ne": True},
            FULFILLMENT_MANUAL_REVIEW_FIELD: {"$ne": True},
        },
        {
            "$set": {
                FULFILLMENT_PENDING_FIELD: True,
                FULFILLMENT_FENCE_FIELD: True,
                FULFILLMENT_ACTION_FIELD: _norm(action_id),
                FULFILLMENT_KIND_FIELD: kind,
                FULFILLMENT_TOKEN_FIELD: token,
                "fulfillment_started_at": _now_iso(),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        cur = await db.sales_deliveries.find_one({"id": sid}, {"_id": 0})
        if cur and cur.get(FULFILLMENT_MANUAL_REVIEW_FIELD) is True:
            raise HTTPException(
                409,
                {
                    "message": "Fulfillment memerlukan manual review. One-shot dinonaktifkan.",
                    "status": OUTCOME_MANUAL_REVIEW,
                    "code": "ONESHOT_MANUAL_REVIEW_REQUIRED",
                },
            )
        if cur and _norm(cur.get(FULFILLMENT_ACTION_FIELD)) == _norm(action_id):
            return {
                "fenced": True,
                "replayed": True,
                "token": _norm(cur.get(FULFILLMENT_TOKEN_FIELD)),
                "doc": cur,
            }
        raise HTTPException(
            409,
            {
                "message": "One-shot fulfillment bentrok: SJ sedang diproses worker lain. Fail-closed.",
                "status": OUTCOME_IN_PROGRESS,
                "code": "ONESHOT_FENCE_CONFLICT",
            },
        )
    return {"fenced": True, "replayed": False, "token": token, "doc": result}


async def _clear_fulfillment_fence(
    db,
    delivery_id: str,
    *,
    token: str,
    success: bool,
    last_status: Optional[str] = None,
    progress: Optional[List[dict]] = None,
    manual_review: bool = False,
) -> bool:
    """Conditional clear by token. Returns True if this token owned the clear."""
    sid = _norm(delivery_id)
    unset = {
        FULFILLMENT_PENDING_FIELD: "",
        FULFILLMENT_FENCE_FIELD: "",
        FULFILLMENT_TOKEN_FIELD: "",
        "fulfillment_started_at": "",
    }
    if success:
        unset[FULFILLMENT_ACTION_FIELD] = ""
        unset[FULFILLMENT_KIND_FIELD] = ""
    status = last_status or ("complete" if success else "partial_failed")
    update_set: Dict[str, Any] = {
        "fulfillment_last_status": status,
        "fulfillment_updated_at": _now_iso(),
    }
    if progress is not None:
        update_set[FULFILLMENT_PROGRESS_FIELD] = progress
    # Always set (never also unset) manual_review to avoid Mongo path conflict
    update_set[FULFILLMENT_MANUAL_REVIEW_FIELD] = True if manual_review else False
    res = await db.sales_deliveries.update_one(
        {"id": sid, FULFILLMENT_TOKEN_FIELD: _norm(token)},
        {"$unset": unset, "$set": update_set},
    )
    return bool(getattr(res, "modified_count", 0) or getattr(res, "matched_count", 0))


async def _healer_takeover_fence(
    db, delivery_id: str, *, healer_action_id: str, kind: str
) -> dict:
    """Take ownership of a stuck fence so the old worker token can no longer write."""
    sid = _norm(delivery_id)
    new_token = str(uuid.uuid4())
    from pymongo import ReturnDocument

    result = await db.sales_deliveries.find_one_and_update(
        {
            "id": sid,
            "$or": [
                {FULFILLMENT_PENDING_FIELD: True},
                {FULFILLMENT_FENCE_FIELD: True},
            ],
            "is_cancelled": {"$ne": True},
        },
        {
            "$set": {
                FULFILLMENT_PENDING_FIELD: True,
                FULFILLMENT_FENCE_FIELD: True,
                FULFILLMENT_ACTION_FIELD: _norm(healer_action_id),
                FULFILLMENT_KIND_FIELD: _norm(kind) or "loading",
                FULFILLMENT_TOKEN_FIELD: new_token,
                "fulfillment_healer_at": _now_iso(),
                "fulfillment_healer_action_id": _norm(healer_action_id),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        # Another healer may have cleared already — re-read
        cur = await db.sales_deliveries.find_one({"id": sid}, {"_id": 0})
        return {"taken": False, "token": None, "doc": cur}
    return {"taken": True, "token": new_token, "doc": result}


async def reconcile_oneshot_fulfillment(
    db,
    delivery_id: str,
    *,
    mutate: bool = False,
    healer_action_id: Optional[str] = None,
) -> dict:
    """Reconcile stuck/partial fulfillment. mutate=False is read-only."""
    inspection = await inspect_fulfillment_recovery(db, delivery_id)
    if not mutate:
        return {**inspection, "mutated": False}

    delivery = await require_operational_sales_delivery(db, delivery_id, attempt_reconcile=True)
    kind = _norm(inspection.get("fulfillment_kind")) or _norm(delivery.get(FULFILLMENT_KIND_FIELD)) or "loading"
    fence_active = bool(inspection.get("fence_active"))
    progress = inspection.get("source_progress") or []
    outcome = inspection.get("status")

    if outcome == OUTCOME_MANUAL_REVIEW:
        # Persist manual review; release fence so operators can use per-PO, but block oneshot resume
        if fence_active:
            takeover = await _healer_takeover_fence(
                db,
                delivery_id,
                healer_action_id=_norm(healer_action_id) or f"heal-{uuid.uuid4().hex[:12]}",
                kind=kind,
            )
            if takeover.get("taken") and takeover.get("token"):
                await _clear_fulfillment_fence(
                    db,
                    delivery_id,
                    token=str(takeover["token"]),
                    success=False,
                    last_status=OUTCOME_MANUAL_REVIEW,
                    progress=progress,
                    manual_review=True,
                )
        else:
            await db.sales_deliveries.update_one(
                {"id": _norm(delivery_id)},
                {
                    "$set": {
                        FULFILLMENT_MANUAL_REVIEW_FIELD: True,
                        FULFILLMENT_PROGRESS_FIELD: progress,
                        "fulfillment_last_status": OUTCOME_MANUAL_REVIEW,
                        "fulfillment_updated_at": _now_iso(),
                    }
                },
            )
        return {**(await inspect_fulfillment_recovery(db, delivery_id)), "mutated": True}

    if outcome == OUTCOME_COMPLETE:
        if fence_active:
            takeover = await _healer_takeover_fence(
                db,
                delivery_id,
                healer_action_id=_norm(healer_action_id) or f"heal-{uuid.uuid4().hex[:12]}",
                kind=kind,
            )
            if takeover.get("taken") and takeover.get("token"):
                await _clear_fulfillment_fence(
                    db,
                    delivery_id,
                    token=str(takeover["token"]),
                    success=True,
                    last_status=OUTCOME_COMPLETE,
                    progress=progress,
                    manual_review=False,
                )
            # If not taken, fence already cleared by concurrent healer — OK
        else:
            await db.sales_deliveries.update_one(
                {"id": _norm(delivery_id)},
                {
                    "$set": {
                        "fulfillment_last_status": OUTCOME_COMPLETE,
                        FULFILLMENT_PROGRESS_FIELD: progress,
                        FULFILLMENT_MANUAL_REVIEW_FIELD: False,
                        "fulfillment_updated_at": _now_iso(),
                    }
                },
            )
        return {**(await inspect_fulfillment_recovery(db, delivery_id)), "mutated": True}

    # partial / recovery_required / in_progress with evidence
    if fence_active:
        takeover = await _healer_takeover_fence(
            db,
            delivery_id,
            healer_action_id=_norm(healer_action_id) or f"heal-{uuid.uuid4().hex[:12]}",
            kind=kind,
        )
        if not takeover.get("taken"):
            # Concurrent healer won — return fresh inspection
            return {**(await inspect_fulfillment_recovery(db, delivery_id)), "mutated": False}
        last = OUTCOME_PARTIAL if inspection.get("complete_sources") else OUTCOME_RECOVERY_REQUIRED
        resume = bool(inspection.get("pending_sources")) and not inspection.get("ambiguous_sources")
        await _clear_fulfillment_fence(
            db,
            delivery_id,
            token=str(takeover["token"]),
            success=False,
            last_status="partial_failed" if last == OUTCOME_PARTIAL else last,
            progress=progress,
            manual_review=False,
        )
        await db.sales_deliveries.update_one(
            {"id": _norm(delivery_id)},
            {
                "$set": {
                    "fulfillment_resume_available": resume,
                    "fulfillment_updated_at": _now_iso(),
                }
            },
        )
    else:
        await db.sales_deliveries.update_one(
            {"id": _norm(delivery_id)},
            {
                "$set": {
                    FULFILLMENT_PROGRESS_FIELD: progress,
                    "fulfillment_last_status": "partial_failed"
                    if inspection.get("complete_sources")
                    else _norm(delivery.get("fulfillment_last_status")) or OUTCOME_RECOVERY_REQUIRED,
                    "fulfillment_resume_available": bool(inspection.get("resume_available")),
                    "fulfillment_updated_at": _now_iso(),
                }
            },
        )
    return {**(await inspect_fulfillment_recovery(db, delivery_id)), "mutated": True}


def _replay_response_from_inspection(inspection: dict, *, action_id: str) -> dict:
    """Never claim ok=true unless evidence shows complete."""
    status = inspection.get("status")
    base = {
        "ok": bool(inspection.get("ok")) and status == OUTCOME_COMPLETE,
        "idempotent": True,
        "replay": True,
        "action_id": action_id,
        "sales_delivery_id": inspection.get("sales_delivery_id"),
        "status": status,
        "complete_sources": inspection.get("complete_sources") or [],
        "pending_sources": inspection.get("pending_sources") or [],
        "ambiguous_sources": inspection.get("ambiguous_sources") or [],
        "resume_available": inspection.get("resume_available"),
        "reason": inspection.get("reason") or "",
        "slice_fulfillment": inspection.get("slice_fulfillment"),
        "source_progress": inspection.get("source_progress"),
        "fulfillment_manual_review_required": inspection.get("fulfillment_manual_review_required"),
    }
    if status == OUTCOME_COMPLETE:
        return base
    # Fail-closed structured 409 body for callers that raise
    raise HTTPException(
        409,
        {
            "message": inspection.get("reason")
            or "Replay one-shot tidak complete menurut bukti aktual.",
            "code": "ONESHOT_REPLAY_NOT_COMPLETE",
            **base,
            "ok": False,
        },
    )


async def assess_oneshot_capabilities(db, delivery_id: str) -> dict:
    """Read-only capability + slice status + recovery gates for UI."""
    delivery = await require_operational_sales_delivery(db, delivery_id, attempt_reconcile=True)
    if delivery_is_cancelled(delivery):
        return {
            "sales_delivery_id": _norm(delivery.get("id")),
            "is_multi_source": is_multi_source_delivery(delivery),
            "oneshot_loading_available": False,
            "oneshot_pod_available": False,
            "resume_loading_available": False,
            "resume_pod_available": False,
            "reason": ONESHOT_BLOCKED_CANCELLED,
            "slice_fulfillment": {"complete": False, "sources": []},
        }
    recovery = await inspect_fulfillment_recovery(db, delivery_id)
    orders = await load_orders_for_delivery(db, delivery)
    slice_info = recovery.get("slice_fulfillment") or assess_multi_source_slice_fulfillment(delivery, orders)
    multi = bool(slice_info.get("is_multi_source")) or is_multi_source_delivery(delivery)
    engine_ok = bool(recovery.get("engine_transactions_available"))
    engine_reason = "" if engine_ok else ONESHOT_BLOCKED_STANDALONE
    missing_mirrors = [s for s in (slice_info.get("sources") or []) if not s.get("has_mirror")]
    mirror_ok = not missing_mirrors

    loading_available = True
    pod_available = True
    reason = ""
    if multi and not engine_ok:
        loading_available = False
        pod_available = False
        reason = engine_reason
    elif not mirror_ok:
        loading_available = False
        pod_available = False
        reason = ONESHOT_MISSING_MIRROR

    manual = bool(recovery.get("fulfillment_manual_review_required")) or recovery.get("status") == OUTCOME_MANUAL_REVIEW
    fence_active = bool(recovery.get("fence_active"))
    last_status = _norm(recovery.get("fulfillment_last_status"))
    # Only treat as partial failure when last_status says so, or evidence shows
    # some sources complete and some pending (true mid-flight residue).
    partial_failed = last_status == "partial_failed" or (
        recovery.get("status") in {OUTCOME_PARTIAL, OUTCOME_RECOVERY_REQUIRED}
        and bool(recovery.get("complete_sources"))
        and bool(recovery.get("pending_sources"))
    )

    if manual:
        loading_available = False
        pod_available = False
        reason = recovery.get("reason") or "Manual review required."
    elif fence_active:
        loading_available = False
        pod_available = False
        reason = recovery.get("reason") or "Fulfillment in_progress / fence aktif."
    elif partial_failed:
        # Fresh full oneshot blocked; controlled resume may be available
        loading_available = False
        pod_available = False
        if not reason:
            reason = recovery.get("reason") or (
                "Partial failure — gunakan Lanjutkan sisa bila tersedia, atau workflow per-PO."
            )

    if pod_available and multi and not slice_info.get("loading_complete"):
        pod_available = False
        if not reason:
            reason = "One-shot POD memerlukan Loading lengkap pada semua source PO."

    resume_loading = (
        bool(recovery.get("resume_available"))
        and _norm(recovery.get("fulfillment_kind") or "loading") == "loading"
        and not manual
        and not fence_active
        and engine_ok
        and mirror_ok
        and bool(recovery.get("pending_sources"))
    )
    resume_pod = (
        bool(recovery.get("resume_available"))
        and _norm(recovery.get("fulfillment_kind")) == "pod"
        and not manual
        and not fence_active
        and engine_ok
        and mirror_ok
        and bool(recovery.get("pending_sources"))
        and bool((recovery.get("shared_pod_proof") or {}).get("valid"))
    )
    # Also allow resume POD when loading complete and last status partial on pod
    if (
        not resume_pod
        and not manual
        and not fence_active
        and engine_ok
        and mirror_ok
        and slice_info.get("loading_complete")
        and not slice_info.get("pod_complete")
        and bool(recovery.get("pending_sources"))
        and _shared_proof_valid(delivery.get(SHARED_POD_PROOF_FIELD))
        and last_status in {"partial_failed", OUTCOME_PARTIAL, OUTCOME_RECOVERY_REQUIRED, ""}
    ):
        # pending POD sources with valid shared proof
        resume_pod = True

    shared = recovery.get("shared_pod_proof")
    return {
        "sales_delivery_id": _norm(delivery.get("id")),
        "sj_number": _norm(delivery.get("sj_number")),
        "is_multi_source": multi,
        "oneshot_loading_available": loading_available,
        "oneshot_pod_available": pod_available,
        "resume_loading_available": resume_loading,
        "resume_pod_available": resume_pod,
        "recovery_status": recovery.get("status"),
        "reason": reason or recovery.get("reason") or "",
        "engine_transactions_available": engine_ok,
        "fulfillment_last_status": last_status or None,
        "fulfillment_commit_pending": bool(recovery.get("fulfillment_commit_pending")),
        "fulfillment_recovery_fence": bool(recovery.get("fulfillment_recovery_fence")),
        "fulfillment_manual_review_required": manual,
        "partial_failed": bool(partial_failed) or last_status == "partial_failed",
        "complete_sources": recovery.get("complete_sources") or [],
        "pending_sources": recovery.get("pending_sources") or [],
        "ambiguous_sources": recovery.get("ambiguous_sources") or [],
        "source_progress": recovery.get("source_progress") or [],
        "slice_fulfillment": slice_info,
        "shared_pod_proof": shared,
    }


async def preflight_loading_all(db, delivery_id: str) -> dict:
    delivery = await require_operational_sales_delivery(db, delivery_id, attempt_reconcile=True)
    if delivery_is_cancelled(delivery):
        raise HTTPException(409, ONESHOT_BLOCKED_CANCELLED)
    if delivery.get(FULFILLMENT_MANUAL_REVIEW_FIELD) is True:
        raise HTTPException(
            409,
            {
                "message": "Fulfillment memerlukan manual review.",
                "status": OUTCOME_MANUAL_REVIEW,
                "code": "ONESHOT_MANUAL_REVIEW_REQUIRED",
            },
        )
    orders = await load_orders_for_delivery(db, delivery)
    slice_info = assess_multi_source_slice_fulfillment(delivery, orders)
    multi = bool(slice_info.get("is_multi_source")) or len(orders) > 1
    if multi:
        ok, reason = await oneshot_engine_allows_multi(db)
        if not ok:
            raise HTTPException(409, reason)
    missing = [s for s in (slice_info.get("sources") or []) if not s.get("has_mirror")]
    if missing:
        raise HTTPException(409, ONESHOT_MISSING_MIRROR)

    from quantity_rules import (
        build_sj_loading_lines,
        validate_loading_match_confirmation,
    )

    sj_id = _norm(delivery.get("id"))
    pending_sources: List[dict] = []
    already_complete: List[str] = []
    for po_id, order in orders.items():
        mirror = _find_mirror(order, delivery)
        if not mirror:
            raise HTTPException(409, ONESHOT_MISSING_MIRROR)
        surat_id = _norm(mirror.get("sales_delivery_id") or mirror.get("id") or sj_id)
        err = validate_loading_match_confirmation(order, surat_id)
        if err:
            remaining = build_sj_loading_lines(order, surat_id)
            if remaining:
                raise HTTPException(400, f"PO {po_id}: {err}")
            already_complete.append(po_id)
            continue
        remaining = build_sj_loading_lines(order, surat_id)
        if not remaining:
            already_complete.append(po_id)
            continue
        from services.physical_stock_engine import precheck_loading_confirmed

        await precheck_loading_confirmed(db, order, remaining)
        pending_sources.append(
            {
                "source_po_id": po_id,
                "surat_jalan_id": surat_id,
                "lines": remaining,
                "slice_qty": sum(_as_int(x.get("qty")) for x in slice_lines_for_po(delivery, po_id)),
            }
        )
    return {
        "delivery": delivery,
        "orders": orders,
        "slice_fulfillment": slice_info,
        "pending_sources": pending_sources,
        "already_complete": already_complete,
        "is_multi_source": multi,
        "sales_delivery_id": sj_id,
    }


def build_shared_pod_proof(
    *,
    recipient_name: str,
    photo_refs: Sequence[dict],
    signature_ref: Optional[dict] = None,
    driver_signature_ref: Optional[dict] = None,
    proof_id: Optional[str] = None,
) -> dict:
    name = _norm(recipient_name)
    if not name:
        raise HTTPException(400, "Nama penerima wajib untuk shared POD proof.")
    photos = [p for p in (photo_refs or []) if isinstance(p, dict) and _norm(p.get("id"))]
    if len(photos) < 2:
        raise HTTPException(400, "Shared POD proof memerlukan minimal 2 foto bukti kirim.")
    if not isinstance(signature_ref, dict) or not _norm(
        signature_ref.get("id") or signature_ref.get("data_url") or signature_ref.get("storage_path")
    ):
        raise HTTPException(400, "Tanda tangan penerima wajib pada shared POD proof.")
    if not isinstance(driver_signature_ref, dict) or not _norm(
        driver_signature_ref.get("id") or driver_signature_ref.get("data_url") or driver_signature_ref.get("storage_path")
    ):
        raise HTTPException(400, "Tanda tangan driver wajib pada shared POD proof.")
    return {
        "id": _norm(proof_id) or str(uuid.uuid4()),
        "recipient_name": name,
        "photo_refs": [
            {
                "id": _norm(p.get("id")),
                "storage_path": p.get("storage_path") or p.get("path"),
                "url": p.get("url"),
                "filename": p.get("filename") or p.get("name"),
                "shipment_id": _norm(p.get("shipment_id")),
                "shared": True,
            }
            for p in photos
        ],
        "signature_ref": dict(signature_ref),
        "driver_signature_ref": dict(driver_signature_ref),
        "created_at": _now_iso(),
    }


def harvest_shared_proof_from_order(order: dict, delivery: dict) -> Optional[dict]:
    from quantity_rules import valid_delivery_proofs_for_shipment

    mirror = _find_mirror(order, delivery)
    if not mirror:
        return None
    photos = valid_delivery_proofs_for_shipment(order, mirror)
    if len(photos) < 2:
        return None
    sig = order.get("delivery_recipient_signature")
    drv = order.get("delivery_driver_signature")
    if not isinstance(sig, dict) or not isinstance(drv, dict):
        return None
    try:
        return build_shared_pod_proof(
            recipient_name=str(sig.get("recipient_name") or ""),
            photo_refs=photos,
            signature_ref=sig,
            driver_signature_ref=drv,
        )
    except HTTPException:
        return None


def attach_shared_proof_refs_to_order(
    order: dict, *, delivery: dict, shared_proof: dict
) -> dict:
    """Reference shared proof metadata on a source PO without re-uploading bytes."""
    sj_id = _norm(delivery.get("id"))
    mirror = _find_mirror(order, delivery)
    shipment_id = _norm((mirror or {}).get("id") or (mirror or {}).get("sales_delivery_id") or sj_id)
    proofs = list(order.get("delivery_proofs") or [])
    existing_ids = {_norm(p.get("id")) for p in proofs if isinstance(p, dict)}
    for photo in shared_proof.get("photo_refs") or []:
        if not isinstance(photo, dict):
            continue
        pid = _norm(photo.get("id"))
        if not pid or pid in existing_ids:
            for p in proofs:
                if isinstance(p, dict) and _norm(p.get("id")) == pid:
                    p.setdefault("shipment_id", shipment_id)
                    p["shared_proof_id"] = _norm(shared_proof.get("id"))
            continue
        proofs.append(
            {
                "id": pid,
                "storage_path": photo.get("storage_path") or photo.get("path"),
                "url": photo.get("url"),
                "filename": photo.get("filename") or photo.get("name") or f"{pid}.jpg",
                "shipment_id": shipment_id,
                "shared_proof_id": _norm(shared_proof.get("id")),
                "kind": "delivery-proof",
                "shared_ref": True,
            }
        )
        existing_ids.add(pid)
    order["delivery_proofs"] = proofs

    sig = dict(shared_proof.get("signature_ref") or {})
    sig["shipment_id"] = shipment_id
    sig["recipient_name"] = _norm(shared_proof.get("recipient_name") or sig.get("recipient_name"))
    sig["shared_proof_id"] = _norm(shared_proof.get("id"))
    order["delivery_recipient_signature"] = sig

    drv = dict(shared_proof.get("driver_signature_ref") or {})
    drv["shipment_id"] = shipment_id
    drv["shared_proof_id"] = _norm(shared_proof.get("id"))
    order["delivery_driver_signature"] = drv
    return order


async def persist_shared_pod_proof(db, delivery_id: str, shared_proof: dict) -> dict:
    sid = _norm(delivery_id)
    existing = await db.sales_deliveries.find_one({"id": sid}, {"_id": 0, SHARED_POD_PROOF_FIELD: 1})
    prev = (existing or {}).get(SHARED_POD_PROOF_FIELD)
    # Preserve stable id if already stored and valid
    if isinstance(prev, dict) and _norm(prev.get("id")):
        if _norm(shared_proof.get("id")) and _norm(shared_proof.get("id")) != _norm(prev.get("id")):
            # Do not replace with a different proof id silently — keep existing
            if _shared_proof_valid(prev):
                return prev
        shared_proof = {**shared_proof, "id": _norm(prev.get("id"))}
    await db.sales_deliveries.update_one(
        {"id": sid},
        {"$set": {SHARED_POD_PROOF_FIELD: shared_proof, "updated_at": _now_iso()}},
    )
    return shared_proof


async def preflight_pod_all(db, delivery_id: str, *, shared_proof: Optional[dict] = None) -> dict:
    delivery = await require_operational_sales_delivery(db, delivery_id, attempt_reconcile=True)
    if delivery_is_cancelled(delivery):
        raise HTTPException(409, ONESHOT_BLOCKED_CANCELLED)
    if delivery.get(FULFILLMENT_MANUAL_REVIEW_FIELD) is True:
        raise HTTPException(
            409,
            {
                "message": "Fulfillment memerlukan manual review.",
                "status": OUTCOME_MANUAL_REVIEW,
                "code": "ONESHOT_MANUAL_REVIEW_REQUIRED",
            },
        )
    orders = await load_orders_for_delivery(db, delivery)
    slice_info = assess_multi_source_slice_fulfillment(delivery, orders)
    multi = bool(slice_info.get("is_multi_source")) or len(orders) > 1
    if multi:
        ok, reason = await oneshot_engine_allows_multi(db)
        if not ok:
            raise HTTPException(409, reason)
    missing = [s for s in (slice_info.get("sources") or []) if not s.get("has_mirror")]
    if missing:
        raise HTTPException(409, ONESHOT_MISSING_MIRROR)
    if not slice_info.get("loading_complete"):
        incomplete = [
            s.get("source_po_id")
            for s in (slice_info.get("sources") or [])
            if not s.get("loading_complete")
        ]
        raise HTTPException(
            409,
            "One-shot POD ditolak: Loading belum lengkap untuk source: "
            + ", ".join(str(x) for x in incomplete[:8]),
        )

    proof = shared_proof
    if proof is None and isinstance(delivery.get(SHARED_POD_PROOF_FIELD), dict):
        proof = delivery.get(SHARED_POD_PROOF_FIELD)
    if proof is None:
        for order in orders.values():
            harvested = harvest_shared_proof_from_order(order, delivery)
            if harvested:
                proof = harvested
                break
    if proof is None or not _shared_proof_valid(
        proof if _shared_proof_valid(proof) else None
    ) and not (
        isinstance(proof, dict)
        and _norm(proof.get("recipient_name"))
    ):
        # Try normalize via build; if fails → manual review signal
        if proof is None:
            raise HTTPException(
                409,
                {
                    "message": (
                        "Shared POD proof belum tersedia. Upload foto + tanda tangan pada satu PO sumber "
                        "atau kirim shared_pod_proof pada request one-shot."
                    ),
                    "status": OUTCOME_MANUAL_REVIEW,
                    "code": "ONESHOT_SHARED_PROOF_MISSING",
                },
            )
    try:
        proof = build_shared_pod_proof(
            recipient_name=str(proof.get("recipient_name") or ""),
            photo_refs=proof.get("photo_refs") or proof.get("photos") or [],
            signature_ref=proof.get("signature_ref") or proof.get("recipient_signature"),
            driver_signature_ref=proof.get("driver_signature_ref") or proof.get("driver_signature"),
            proof_id=_norm(proof.get("id")) or None,
        )
    except HTTPException as exc:
        raise HTTPException(
            409,
            {
                "message": str(getattr(exc, "detail", None) or exc),
                "status": OUTCOME_MANUAL_REVIEW,
                "code": "ONESHOT_SHARED_PROOF_INVALID",
            },
        ) from exc

    pending: List[dict] = []
    already: List[str] = []
    for src in slice_info.get("sources") or []:
        po_id = _norm(src.get("source_po_id"))
        if src.get("delivered"):
            already.append(po_id)
            continue
        pending.append(
            {
                "source_po_id": po_id,
                "shipment_id": _norm(src.get("shipment_id") or delivery.get("id")),
                "slice_qty": _as_int(src.get("slice_qty")),
            }
        )
    return {
        "delivery": delivery,
        "orders": orders,
        "slice_fulfillment": slice_info,
        "pending_sources": pending,
        "already_complete": already,
        "shared_proof": proof,
        "is_multi_source": multi,
        "sales_delivery_id": _norm(delivery.get("id")),
    }


async def confirm_loading_all_orchestrated(
    db,
    delivery_id: str,
    user: dict,
    *,
    action_id: str,
    apply_one,
    resume: bool = False,
) -> dict:
    """Orchestrate Loading across sources. ``apply_one(po_id, surat_jalan_id, child_action_id)``."""
    plan = await preflight_loading_all(db, delivery_id)
    action_id = _norm(action_id) or str(uuid.uuid4())
    if not plan["pending_sources"]:
        progress = build_source_progress_from_evidence(
            plan["delivery"], plan["orders"], kind="loading", action_id=action_id
        )
        await db.sales_deliveries.update_one(
            {"id": plan["sales_delivery_id"]},
            {
                "$set": {
                    "fulfillment_last_status": OUTCOME_COMPLETE,
                    FULFILLMENT_PROGRESS_FIELD: progress,
                    FULFILLMENT_MANUAL_REVIEW_FIELD: False,
                    "fulfillment_updated_at": _now_iso(),
                }
            },
        )
        return {
            "ok": True,
            "idempotent": True,
            "status": OUTCOME_COMPLETE,
            "action_id": action_id,
            "sales_delivery_id": plan["sales_delivery_id"],
            "results": [
                {"source_po_id": pid, "status": "already_loaded"} for pid in plan["already_complete"]
            ],
            "complete_sources": list(plan["already_complete"]),
            "pending_sources": [],
            "slice_fulfillment": plan["slice_fulfillment"],
        }

    if resume:
        # Only pending — already enforced by preflight skipping complete
        pass

    fence = await _acquire_fulfillment_fence(
        db, plan["sales_delivery_id"], action_id=action_id, kind="loading"
    )
    if fence.get("replayed"):
        inspection = await inspect_fulfillment_recovery(db, plan["sales_delivery_id"])
        return _replay_response_from_inspection(inspection, action_id=action_id)

    results: List[dict] = [
        {"source_po_id": pid, "status": "already_loaded"} for pid in plan["already_complete"]
    ]
    applied: List[dict] = []
    try:
        for src in plan["pending_sources"]:
            po_id = src["source_po_id"]
            child_action = f"{action_id}:{po_id}"
            try:
                one = await apply_one(po_id, src["surat_jalan_id"], child_action)
            except Exception as exc:
                results.append(
                    {
                        "source_po_id": po_id,
                        "status": "failed",
                        "error": str(getattr(exc, "detail", None) or exc),
                    }
                )
                raise
            applied.append({"source_po_id": po_id, **(one if isinstance(one, dict) else {})})
            results.append({"source_po_id": po_id, "status": "loaded", "detail": one})
        orders = await load_orders_for_delivery(db, plan["delivery"])
        progress = build_source_progress_from_evidence(
            plan["delivery"], orders, kind="loading", action_id=action_id
        )
        cleared = await _clear_fulfillment_fence(
            db,
            plan["sales_delivery_id"],
            token=str(fence.get("token") or ""),
            success=True,
            last_status=OUTCOME_COMPLETE,
            progress=progress,
        )
        if not cleared:
            inspection = await inspect_fulfillment_recovery(db, plan["sales_delivery_id"])
            if inspection.get("status") != OUTCOME_COMPLETE:
                raise HTTPException(
                    409,
                    {
                        "message": "Fence diambil alih healer sebelum finalisasi. Reconcile diperlukan.",
                        "code": "ONESHOT_FENCE_TAKEOVER",
                        "status": inspection.get("status"),
                        **{k: inspection.get(k) for k in (
                            "complete_sources", "pending_sources", "ambiguous_sources", "slice_fulfillment"
                        )},
                    },
                )
        slice_after = assess_multi_source_slice_fulfillment(plan["delivery"], orders)
        return {
            "ok": True,
            "idempotent": False,
            "status": OUTCOME_COMPLETE,
            "action_id": action_id,
            "sales_delivery_id": plan["sales_delivery_id"],
            "results": results,
            "complete_sources": [p["po_id"] for p in progress if p.get("status") == "complete"],
            "pending_sources": [],
            "slice_fulfillment": slice_after,
            "source_progress": progress,
        }
    except Exception as exc:
        if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
            code = str(exc.detail.get("code") or "")
            if code in {"ONESHOT_FENCE_TAKEOVER", "ONESHOT_REPLAY_NOT_COMPLETE"}:
                raise
        orders = await load_orders_for_delivery(db, plan["delivery"])
        progress = build_source_progress_from_evidence(
            plan["delivery"], orders, kind="loading", action_id=action_id
        )
        await _clear_fulfillment_fence(
            db,
            plan["sales_delivery_id"],
            token=str(fence.get("token") or ""),
            success=False,
            last_status="partial_failed",
            progress=progress,
        )
        slice_after = assess_multi_source_slice_fulfillment(plan["delivery"], orders)
        raise HTTPException(
            409,
            {
                "message": (
                    "One-shot Loading gagal setelah sebagian source. "
                    "Status per-PO dipertahankan; SJ gabungan tidak di-complete. "
                    "Lanjutkan sisa atau workflow per-PO."
                ),
                "action_id": action_id,
                "applied": applied,
                "results": results,
                "status": OUTCOME_PARTIAL,
                "complete_sources": [p["po_id"] for p in progress if p.get("status") == "complete"],
                "pending_sources": [p["po_id"] for p in progress if p.get("status") == "pending"],
                "slice_fulfillment": slice_after,
                "source_progress": progress,
                "resume_available": True,
                "code": "ONESHOT_LOADING_PARTIAL_FAILED",
            },
        ) from exc


async def confirm_delivered_all_orchestrated(
    db,
    delivery_id: str,
    user: dict,
    *,
    action_id: str,
    shared_proof: Optional[dict],
    apply_one,
    attach_proof_fn,
    resume: bool = False,
) -> dict:
    """Orchestrate POD across sources with shared proof references."""
    plan = await preflight_pod_all(db, delivery_id, shared_proof=shared_proof)
    action_id = _norm(action_id) or str(uuid.uuid4())
    proof = plan["shared_proof"]
    # Reuse stable stored proof id
    proof = await persist_shared_pod_proof(db, plan["sales_delivery_id"], proof)

    if not plan["pending_sources"]:
        progress = build_source_progress_from_evidence(
            plan["delivery"], plan["orders"], kind="pod", action_id=action_id
        )
        await db.sales_deliveries.update_one(
            {"id": plan["sales_delivery_id"]},
            {
                "$set": {
                    "fulfillment_last_status": OUTCOME_COMPLETE,
                    FULFILLMENT_PROGRESS_FIELD: progress,
                    FULFILLMENT_MANUAL_REVIEW_FIELD: False,
                    "fulfillment_updated_at": _now_iso(),
                }
            },
        )
        return {
            "ok": True,
            "idempotent": True,
            "status": OUTCOME_COMPLETE,
            "action_id": action_id,
            "sales_delivery_id": plan["sales_delivery_id"],
            "shared_proof_id": proof.get("id"),
            "results": [
                {"source_po_id": pid, "status": "already_delivered"} for pid in plan["already_complete"]
            ],
            "complete_sources": list(plan["already_complete"]),
            "pending_sources": [],
            "slice_fulfillment": plan["slice_fulfillment"],
        }

    fence = await _acquire_fulfillment_fence(
        db, plan["sales_delivery_id"], action_id=action_id, kind="pod"
    )
    if fence.get("replayed"):
        inspection = await inspect_fulfillment_recovery(db, plan["sales_delivery_id"])
        resp = _replay_response_from_inspection(inspection, action_id=action_id)
        resp["shared_proof_id"] = proof.get("id")
        return resp

    results: List[dict] = [
        {"source_po_id": pid, "status": "already_delivered"} for pid in plan["already_complete"]
    ]
    applied: List[dict] = []
    try:
        for src in plan["pending_sources"]:
            po_id = src["source_po_id"]
            child_action = f"{action_id}:{po_id}"
            await attach_proof_fn(po_id, plan["delivery"], proof)
            try:
                one = await apply_one(po_id, src["shipment_id"], child_action, proof)
            except Exception as exc:
                results.append(
                    {
                        "source_po_id": po_id,
                        "status": "failed",
                        "error": str(getattr(exc, "detail", None) or exc),
                    }
                )
                raise
            applied.append({"source_po_id": po_id})
            results.append({"source_po_id": po_id, "status": "delivered", "detail": one})
        orders = await load_orders_for_delivery(db, plan["delivery"])
        progress = build_source_progress_from_evidence(
            plan["delivery"], orders, kind="pod", action_id=action_id
        )
        cleared = await _clear_fulfillment_fence(
            db,
            plan["sales_delivery_id"],
            token=str(fence.get("token") or ""),
            success=True,
            last_status=OUTCOME_COMPLETE,
            progress=progress,
        )
        if not cleared:
            inspection = await inspect_fulfillment_recovery(db, plan["sales_delivery_id"])
            if inspection.get("status") != OUTCOME_COMPLETE:
                raise HTTPException(
                    409,
                    {
                        "message": "Fence diambil alih healer sebelum finalisasi POD. Reconcile diperlukan.",
                        "code": "ONESHOT_FENCE_TAKEOVER",
                        "status": inspection.get("status"),
                        "shared_proof_id": proof.get("id"),
                    },
                )
        slice_after = assess_multi_source_slice_fulfillment(plan["delivery"], orders)
        return {
            "ok": True,
            "idempotent": False,
            "status": OUTCOME_COMPLETE,
            "action_id": action_id,
            "sales_delivery_id": plan["sales_delivery_id"],
            "shared_proof_id": proof.get("id"),
            "results": results,
            "complete_sources": [p["po_id"] for p in progress if p.get("status") == "complete"],
            "pending_sources": [],
            "slice_fulfillment": slice_after,
            "source_progress": progress,
        }
    except Exception as exc:
        if isinstance(exc, HTTPException) and isinstance(exc.detail, dict):
            code = str(exc.detail.get("code") or "")
            if code in {"ONESHOT_FENCE_TAKEOVER", "ONESHOT_REPLAY_NOT_COMPLETE"}:
                raise
        orders = await load_orders_for_delivery(db, plan["delivery"])
        progress = build_source_progress_from_evidence(
            plan["delivery"], orders, kind="pod", action_id=action_id
        )
        await _clear_fulfillment_fence(
            db,
            plan["sales_delivery_id"],
            token=str(fence.get("token") or ""),
            success=False,
            last_status="partial_failed",
            progress=progress,
        )
        slice_after = assess_multi_source_slice_fulfillment(plan["delivery"], orders)
        raise HTTPException(
            409,
            {
                "message": (
                    "One-shot POD gagal setelah sebagian source. "
                    "Status per-PO dan shared proof dipertahankan."
                ),
                "action_id": action_id,
                "shared_proof_id": proof.get("id"),
                "applied": applied,
                "results": results,
                "status": OUTCOME_PARTIAL,
                "complete_sources": [p["po_id"] for p in progress if p.get("status") == "complete"],
                "pending_sources": [p["po_id"] for p in progress if p.get("status") == "pending"],
                "slice_fulfillment": slice_after,
                "source_progress": progress,
                "resume_available": True,
                "code": "ONESHOT_POD_PARTIAL_FAILED",
            },
        ) from exc


async def resume_pending_fulfillment(
    db,
    delivery_id: str,
    user: dict,
    *,
    action_id: Optional[str],
    kind: Optional[str],
    apply_loading,
    apply_pod,
    attach_proof_fn,
    shared_proof: Optional[dict] = None,
) -> dict:
    """Mutating resume for pending sources only after evidence-based gate."""
    # Reconcile first (mutate) to clear stuck fence if needed
    recon = await reconcile_oneshot_fulfillment(
        db,
        delivery_id,
        mutate=True,
        healer_action_id=_norm(action_id) or f"resume-heal-{uuid.uuid4().hex[:10]}",
    )
    if recon.get("status") == OUTCOME_MANUAL_REVIEW:
        raise HTTPException(
            409,
            {
                "message": recon.get("reason") or "Manual review required.",
                "code": "ONESHOT_MANUAL_REVIEW_REQUIRED",
                "status": OUTCOME_MANUAL_REVIEW,
                **{k: recon.get(k) for k in (
                    "complete_sources", "pending_sources", "ambiguous_sources", "slice_fulfillment"
                )},
            },
        )
    if recon.get("status") == OUTCOME_COMPLETE:
        return {
            "ok": True,
            "idempotent": True,
            "status": OUTCOME_COMPLETE,
            "action_id": action_id,
            "sales_delivery_id": delivery_id,
            "message": "Semua source sudah complete menurut bukti. Tidak ada yang dilanjutkan.",
            "slice_fulfillment": recon.get("slice_fulfillment"),
        }
    if not recon.get("resume_available") and not recon.get("pending_sources"):
        raise HTTPException(
            409,
            {
                "message": recon.get("reason") or "Resume tidak tersedia.",
                "code": "ONESHOT_RESUME_UNAVAILABLE",
                "status": recon.get("status"),
            },
        )

    caps = await assess_oneshot_capabilities(db, delivery_id)
    use_kind = _norm(kind) or _norm(recon.get("fulfillment_kind")) or "loading"
    resume_action = _norm(action_id) or f"resume-{uuid.uuid4().hex[:12]}"

    if use_kind == "pod":
        if not caps.get("resume_pod_available") and not (
            caps.get("pending_sources") and (caps.get("shared_pod_proof") or {}).get("valid")
        ):
            raise HTTPException(
                409,
                {
                    "message": caps.get("reason") or "Resume POD tidak aman / tidak tersedia.",
                    "code": "ONESHOT_RESUME_POD_BLOCKED",
                    "status": caps.get("recovery_status"),
                },
            )
        return await confirm_delivered_all_orchestrated(
            db,
            delivery_id,
            user,
            action_id=resume_action,
            shared_proof=shared_proof,
            apply_one=apply_pod,
            attach_proof_fn=attach_proof_fn,
            resume=True,
        )

    if not caps.get("resume_loading_available") and not caps.get("pending_sources"):
        raise HTTPException(
            409,
            {
                "message": caps.get("reason") or "Resume Loading tidak aman / tidak tersedia.",
                "code": "ONESHOT_RESUME_LOADING_BLOCKED",
                "status": caps.get("recovery_status"),
            },
        )
    return await confirm_loading_all_orchestrated(
        db,
        delivery_id,
        user,
        action_id=resume_action,
        apply_one=apply_loading,
        resume=True,
    )
