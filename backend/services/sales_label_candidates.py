"""Pending Accurate Sales labels discovered during PO import (one-time verification).

Import must succeed even if candidate bookkeeping fails.
Never auto-creates Master Sales. Never writes sales_user_mapping by default.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services import sales_master as sm
from services import sales_master_aliases as aliases
from services.sales_user_mapping import exact_sales_label

COLLECTION = "sales_label_candidates"
AUDIT_COLLECTION = "audit_logs"

STATUS_PENDING = "pending"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_PROCESSING = "processing"
STATUS_APPROVED_NEW = "approved_new"
STATUS_LINKED = "linked"

OPEN_STATUSES = frozenset({STATUS_PENDING, STATUS_NEEDS_REVIEW, STATUS_PROCESSING})
DONE_STATUSES = frozenset({STATUS_APPROVED_NEW, STATUS_LINKED})

SAMPLE_PO_LIMIT = 5


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def actor_fields(user: Optional[dict]) -> dict:
    user = user or {}
    return {
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role"),
        "id": str(user.get("user_id") or user.get("id") or user.get("sub") or "").strip() or None,
    }


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("exact_label", unique=True)
    await col.create_index([("status", 1), ("last_seen_at", -1)])


def public_candidate(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "exact_label": exact_sales_label(doc.get("exact_label")),
        "status": doc.get("status") or STATUS_PENDING,
        "sales_master_id": str(doc.get("sales_master_id") or "").strip() or None,
        "seen_count": int(doc.get("seen_count") or 0),
        "first_seen_at": doc.get("first_seen_at"),
        "last_seen_at": doc.get("last_seen_at"),
        "sample_po_ids": list(doc.get("sample_po_ids") or [])[:SAMPLE_PO_LIMIT],
        "review_reason": doc.get("review_reason") or None,
        "resolved_at": doc.get("resolved_at"),
        "resolved_by": doc.get("resolved_by"),
        "updated_at": doc.get("updated_at"),
    }


async def _write_audit(db, *, action: str, actor: dict, before=None, after=None, note: str = "") -> None:
    await db[AUDIT_COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "action": action,
        "actor_email": (actor or {}).get("email"),
        "actor_name": (actor or {}).get("name"),
        "actor_role": (actor or {}).get("role"),
        "before": before,
        "after": after,
        "note": note,
        "at": now_iso(),
    })


async def record_unrecognized_label(
    db,
    *,
    sales_label: Any,
    po_id: Optional[str] = None,
    review_reason: Optional[str] = None,
    status: str = STATUS_PENDING,
) -> Optional[dict]:
    """Atomic upsert by exact_label. Returns public candidate or None if blank.

    Safe to call from import path; callers should catch and ignore failures.
    """
    label = exact_sales_label(sales_label)
    if not label:
        return None

    # Already bound to a Master → no open candidate needed.
    mid, alias_status = await aliases.resolve_master_id_for_label(db, label)
    if alias_status == "matched" and mid and not review_reason:
        # Close any leftover open candidate idempotently.
        await db[COLLECTION].update_one(
            {"exact_label": label, "status": {"$in": list(OPEN_STATUSES)}},
            {"$set": {
                "status": STATUS_LINKED,
                "sales_master_id": mid,
                "resolved_at": now_iso(),
                "updated_at": now_iso(),
                "review_reason": None,
            }},
        )
        return None

    now = now_iso()
    po = str(po_id or "").strip()
    desired_status = status if status in (STATUS_PENDING, STATUS_NEEDS_REVIEW) else STATUS_PENDING

    existing = await db[COLLECTION].find_one({"exact_label": label}, {"_id": 0})
    if existing:
        st = str(existing.get("status") or "")
        if st in DONE_STATUSES and not review_reason:
            # Already verified — bump last_seen only for telemetry, keep done.
            updates: Dict[str, Any] = {"last_seen_at": now, "updated_at": now}
            inc = {"seen_count": 1}
            push = None
            if po:
                push = {
                    "sample_po_ids": {
                        "$each": [po],
                        "$slice": -SAMPLE_PO_LIMIT,
                    }
                }
            ops: Dict[str, Any] = {"$set": updates, "$inc": inc}
            if push:
                ops["$push"] = push
            await db[COLLECTION].update_one({"exact_label": label}, ops)
            refreshed = await db[COLLECTION].find_one({"exact_label": label}, {"_id": 0})
            return public_candidate(refreshed)

        updates = {
            "last_seen_at": now,
            "updated_at": now,
        }
        if review_reason:
            updates["review_reason"] = review_reason
            updates["status"] = STATUS_NEEDS_REVIEW
        elif st == STATUS_PROCESSING:
            pass  # leave lock
        elif st in OPEN_STATUSES:
            updates["status"] = desired_status if desired_status == STATUS_NEEDS_REVIEW else st
        else:
            updates["status"] = desired_status

        ops = {"$set": updates, "$inc": {"seen_count": 1}}
        if po:
            ops["$push"] = {
                "sample_po_ids": {
                    "$each": [po],
                    "$slice": -SAMPLE_PO_LIMIT,
                }
            }
        await db[COLLECTION].update_one({"exact_label": label}, ops)
        refreshed = await db[COLLECTION].find_one({"exact_label": label}, {"_id": 0})
        return public_candidate(refreshed)

    doc = {
        "id": str(uuid.uuid4()),
        "exact_label": label,
        "status": STATUS_NEEDS_REVIEW if review_reason else desired_status,
        "sales_master_id": None,
        "seen_count": 1,
        "first_seen_at": now,
        "last_seen_at": now,
        "sample_po_ids": [po] if po else [],
        "review_reason": review_reason,
        "resolved_at": None,
        "resolved_by": None,
        "updated_at": now,
    }
    try:
        await db[COLLECTION].insert_one(doc)
        return public_candidate(doc)
    except DuplicateKeyError:
        # Concurrent insert — fall through to update path once.
        return await record_unrecognized_label(
            db,
            sales_label=label,
            po_id=po_id,
            review_reason=review_reason,
            status=status,
        )


async def list_candidates(
    db,
    *,
    status: Optional[str] = None,
    open_only: bool = False,
) -> List[dict]:
    query: Dict[str, Any] = {}
    if open_only:
        query["status"] = {"$in": [STATUS_PENDING, STATUS_NEEDS_REVIEW]}
    elif status:
        query["status"] = str(status).strip()
    rows = await db[COLLECTION].find(query, {"_id": 0}).sort([
        ("last_seen_at", -1),
        ("exact_label", 1),
    ]).to_list(2000)
    return [public_candidate(r) for r in rows if r]


async def get_candidate(db, candidate_id: str) -> Optional[dict]:
    cid = str(candidate_id or "").strip()
    if not cid:
        return None
    doc = await db[COLLECTION].find_one({"id": cid}, {"_id": 0})
    return public_candidate(doc)


async def _claim_open_candidate(db, candidate_id: str) -> dict:
    cid = str(candidate_id or "").strip()
    doc = await db[COLLECTION].find_one({"id": cid}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Kandidat tidak ditemukan")
    st = str(doc.get("status") or "")
    if st in DONE_STATUSES:
        return doc  # idempotent caller handles
    if st == STATUS_PROCESSING:
        # Stale lock recovery: allow same-label completion if alias already bound.
        return doc
    if st not in (STATUS_PENDING, STATUS_NEEDS_REVIEW):
        raise HTTPException(409, f"Status kandidat tidak dapat diverifikasi: {st}")

    claimed = await db[COLLECTION].find_one_and_update(
        {"id": cid, "status": {"$in": [STATUS_PENDING, STATUS_NEEDS_REVIEW]}},
        {"$set": {"status": STATUS_PROCESSING, "updated_at": now_iso()}},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        # Race: re-read
        again = await db[COLLECTION].find_one({"id": cid}, {"_id": 0})
        if again and str(again.get("status") or "") in DONE_STATUSES:
            return again
        raise HTTPException(409, "Kandidat sedang diproses atau sudah selesai")
    return claimed


async def _finalize_candidate(
    db,
    *,
    candidate_id: str,
    status: str,
    sales_master_id: str,
    actor: dict,
) -> dict:
    now = now_iso()
    updated = await db[COLLECTION].find_one_and_update(
        {"id": candidate_id},
        {"$set": {
            "status": status,
            "sales_master_id": sales_master_id,
            "resolved_at": now,
            "resolved_by": actor.get("email") or actor.get("id"),
            "updated_at": now,
            "review_reason": None,
        }},
        projection={"_id": 0},
        return_document=ReturnDocument.AFTER,
    )
    return public_candidate(updated)


async def approve_as_new(
    db,
    *,
    candidate_id: str,
    user: dict,
    sales_name: Optional[str] = None,
    branch_ids: Optional[Sequence[str]] = None,
    active: bool = True,
) -> dict:
    """Create Master Sales + bind alias. Does NOT write sales_user_mapping."""
    await aliases.ensure_indexes(db)
    await ensure_indexes(db)
    await sm.ensure_indexes(db)

    actor = actor_fields(user)
    claimed = await _claim_open_candidate(db, candidate_id)
    label = exact_sales_label(claimed.get("exact_label"))
    if not label:
        raise HTTPException(400, "Label kandidat kosong")

    st = str(claimed.get("status") or "")
    if st in DONE_STATUSES and claimed.get("sales_master_id"):
        return {
            "candidate": public_candidate(claimed),
            "master": await sm.get_sales_master(db, claimed["sales_master_id"]),
            "alias": await aliases.get_alias(db, label),
            "idempotent": True,
        }

    # Recovery: alias already bound for this label.
    existing_alias = await aliases.get_alias(db, label)
    if existing_alias and existing_alias.get("sales_master_id"):
        mid = existing_alias["sales_master_id"]
        cand = await _finalize_candidate(
            db,
            candidate_id=claimed["id"],
            status=STATUS_APPROVED_NEW if st != STATUS_LINKED else STATUS_LINKED,
            sales_master_id=mid,
            actor=actor,
        )
        await _write_audit(
            db,
            action="settings.sales_label_candidate.approve_new",
            actor=actor,
            before=public_candidate(claimed),
            after=cand,
            note="Recovery: alias already bound",
        )
        return {
            "candidate": cand,
            "master": await sm.get_sales_master(db, mid),
            "alias": existing_alias,
            "idempotent": True,
        }

    name = sm.normalize_sales_name(sales_name) if sales_name else sm.normalize_sales_name(label)
    if not name:
        raise HTTPException(400, "Nama Sales wajib diisi")

    # Recovery: master already created under same name_key from partial prior attempt.
    prior = await db[sm.COLLECTION].find_one({"name_key": sm.name_key(name)}, {"_id": 0})
    if prior:
        master_pub = sm.public_sales(prior)
    else:
        payload = {
            "sales_name": name,
            "user_id": None,  # never auto-assign Eternal account
            "branch_ids": list(branch_ids or []),
            "active": bool(active),
            "sort_order": 0,
        }
        master_pub = await sm.create_sales_master(db, payload, user)

    mid = master_pub["id"]
    alias = await aliases.bind_alias(
        db,
        exact_label=label,
        sales_master_id=mid,
        user=user,
        note="Approve candidate as new Master Sales",
    )
    # Also bind official sales_name if it differs (exact) so resolve stays consistent.
    official = exact_sales_label(master_pub.get("sales_name"))
    if official and official != label:
        try:
            await aliases.bind_alias(
                db,
                exact_label=official,
                sales_master_id=mid,
                user=user,
                note="Official sales_name alias",
                allow_same_master=True,
            )
        except HTTPException as exc:
            if exc.status_code != 409:
                raise

    cand = await _finalize_candidate(
        db,
        candidate_id=claimed["id"],
        status=STATUS_APPROVED_NEW,
        sales_master_id=mid,
        actor=actor,
    )
    await _write_audit(
        db,
        action="settings.sales_label_candidate.approve_new",
        actor=actor,
        before=public_candidate(claimed),
        after=cand,
        note="Approve as new Master Sales (no sales_user_mapping write)",
    )
    return {"candidate": cand, "master": master_pub, "alias": alias, "idempotent": False}


async def link_existing(
    db,
    *,
    candidate_id: str,
    sales_master_id: str,
    user: dict,
) -> dict:
    """Bind label → existing Master. Rejects inactive Master. No mapping write."""
    await aliases.ensure_indexes(db)
    await ensure_indexes(db)

    actor = actor_fields(user)
    mid = str(sales_master_id or "").strip()
    if not mid:
        raise HTTPException(400, "sales_master_id wajib diisi")

    master = await sm.get_sales_master(db, mid)
    if not master:
        raise HTTPException(400, "Master Sales tidak ditemukan")
    if not master.get("active"):
        raise HTTPException(409, "Tidak dapat menghubungkan ke Master Sales nonaktif")

    claimed = await _claim_open_candidate(db, candidate_id)
    label = exact_sales_label(claimed.get("exact_label"))
    if not label:
        raise HTTPException(400, "Label kandidat kosong")

    st = str(claimed.get("status") or "")
    if st in DONE_STATUSES:
        existing_mid = str(claimed.get("sales_master_id") or "").strip()
        if existing_mid == mid:
            return {
                "candidate": public_candidate(claimed),
                "master": master,
                "alias": await aliases.get_alias(db, label),
                "idempotent": True,
            }
        raise HTTPException(409, "Kandidat sudah terhubung ke Master Sales lain")

    existing_alias = await aliases.get_alias(db, label)
    if existing_alias:
        other = str(existing_alias.get("sales_master_id") or "").strip()
        if other != mid:
            # Release processing lock back to needs_review on conflict
            await db[COLLECTION].update_one(
                {"id": claimed["id"], "status": STATUS_PROCESSING},
                {"$set": {"status": STATUS_NEEDS_REVIEW, "updated_at": now_iso(),
                          "review_reason": "alias_conflict"}},
            )
            raise HTTPException(
                409,
                f"Label Accurate sudah terhubung ke Master Sales lain: {label}",
            )
        cand = await _finalize_candidate(
            db,
            candidate_id=claimed["id"],
            status=STATUS_LINKED,
            sales_master_id=mid,
            actor=actor,
        )
        await _write_audit(
            db,
            action="settings.sales_label_candidate.link",
            actor=actor,
            before=public_candidate(claimed),
            after=cand,
            note="Idempotent link — alias already bound",
        )
        return {"candidate": cand, "master": master, "alias": existing_alias, "idempotent": True}

    try:
        alias = await aliases.bind_alias(
            db,
            exact_label=label,
            sales_master_id=mid,
            user=user,
            note="Link candidate to existing Master Sales",
        )
    except HTTPException as exc:
        if exc.status_code == 409:
            await db[COLLECTION].update_one(
                {"id": claimed["id"], "status": STATUS_PROCESSING},
                {"$set": {"status": STATUS_NEEDS_REVIEW, "updated_at": now_iso(),
                          "review_reason": "alias_conflict"}},
            )
        raise

    cand = await _finalize_candidate(
        db,
        candidate_id=claimed["id"],
        status=STATUS_LINKED,
        sales_master_id=mid,
        actor=actor,
    )
    await _write_audit(
        db,
        action="settings.sales_label_candidate.link",
        actor=actor,
        before=public_candidate(claimed),
        after=cand,
        note="Link to existing Master Sales (no sales_user_mapping write)",
    )
    return {"candidate": cand, "master": master, "alias": alias, "idempotent": False}
