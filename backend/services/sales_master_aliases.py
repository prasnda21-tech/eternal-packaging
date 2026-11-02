"""Authoritative Accurate label → sales_master.id (exact strip-only).

Does not write sales_user_mapping, users.id, CRM ownership, or financial snapshots.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.sales_user_mapping import exact_sales_label

COLLECTION = "sales_master_aliases"
AUDIT_COLLECTION = "audit_logs"

STATUS_ACTIVE = "active"


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
    await col.create_index([("sales_master_id", 1), ("exact_label", 1)])


def public_alias(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "exact_label": exact_sales_label(doc.get("exact_label")),
        "sales_master_id": str(doc.get("sales_master_id") or "").strip() or None,
        "status": doc.get("status") or STATUS_ACTIVE,
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
    }


async def get_alias(db, label: Any) -> Optional[dict]:
    key = exact_sales_label(label)
    if not key:
        return None
    doc = await db[COLLECTION].find_one({"exact_label": key}, {"_id": 0})
    return public_alias(doc)


async def list_aliases_for_master(db, sales_master_id: str) -> List[dict]:
    mid = str(sales_master_id or "").strip()
    if not mid:
        return []
    rows = await db[COLLECTION].find(
        {"sales_master_id": mid},
        {"_id": 0},
    ).sort([("exact_label", 1)]).to_list(500)
    return [public_alias(r) for r in rows if r]


async def resolve_master_id_for_label(db, label: Any) -> Tuple[Optional[str], str]:
    """Return (sales_master_id, status) where status is matched|empty|unmatched."""
    key = exact_sales_label(label)
    if not key:
        return None, "empty"
    doc = await db[COLLECTION].find_one({"exact_label": key}, {"_id": 0, "sales_master_id": 1})
    mid = str((doc or {}).get("sales_master_id") or "").strip()
    if not mid:
        return None, "unmatched"
    return mid, "matched"


async def bind_alias(
    db,
    *,
    exact_label: str,
    sales_master_id: str,
    user: Optional[dict],
    note: str = "",
    allow_same_master: bool = True,
) -> dict:
    """Atomically bind exact_label → sales_master_id. Conflict with other master → 409."""
    label = exact_sales_label(exact_label)
    mid = str(sales_master_id or "").strip()
    if not label:
        raise HTTPException(400, "Label Accurate wajib diisi")
    if not mid:
        raise HTTPException(400, "sales_master_id wajib diisi")

    existing = await db[COLLECTION].find_one({"exact_label": label}, {"_id": 0})
    if existing:
        other = str(existing.get("sales_master_id") or "").strip()
        if other == mid and allow_same_master:
            return public_alias(existing)
        raise HTTPException(
            409,
            f"Label Accurate sudah terhubung ke Master Sales lain: {label}",
        )

    actor = actor_fields(user)
    doc = {
        "id": str(uuid.uuid4()),
        "exact_label": label,
        "sales_master_id": mid,
        "status": STATUS_ACTIVE,
        "created_at": now_iso(),
        "created_by": actor.get("email") or actor.get("id"),
        "updated_at": now_iso(),
    }
    try:
        await db[COLLECTION].insert_one(doc)
    except DuplicateKeyError:
        raced = await db[COLLECTION].find_one({"exact_label": label}, {"_id": 0})
        other = str((raced or {}).get("sales_master_id") or "").strip()
        if other == mid and allow_same_master:
            return public_alias(raced)
        raise HTTPException(
            409,
            f"Label Accurate sudah terhubung ke Master Sales lain: {label}",
        )

    await db[AUDIT_COLLECTION].insert_one({
        "id": str(uuid.uuid4()),
        "action": "settings.sales_master_alias.bind",
        "actor_email": actor.get("email"),
        "actor_name": actor.get("name"),
        "actor_role": actor.get("role"),
        "before": None,
        "after": public_alias(doc),
        "note": note or "Bind Accurate label → sales_master.id",
        "at": now_iso(),
    })
    return public_alias(doc)


async def ensure_alias_silent(
    db,
    *,
    exact_label: str,
    sales_master_id: str,
) -> Optional[dict]:
    """Insert alias if missing; no-op if same master. No audit (import hot path).

    Returns public alias or None on blank/conflict-with-other-master.
    """
    label = exact_sales_label(exact_label)
    mid = str(sales_master_id or "").strip()
    if not label or not mid:
        return None
    existing = await db[COLLECTION].find_one({"exact_label": label}, {"_id": 0})
    if existing:
        other = str(existing.get("sales_master_id") or "").strip()
        return public_alias(existing) if other == mid else None
    doc = {
        "id": str(uuid.uuid4()),
        "exact_label": label,
        "sales_master_id": mid,
        "status": STATUS_ACTIVE,
        "created_at": now_iso(),
        "created_by": "system:import",
        "updated_at": now_iso(),
    }
    try:
        await db[COLLECTION].insert_one(doc)
        return public_alias(doc)
    except DuplicateKeyError:
        raced = await db[COLLECTION].find_one({"exact_label": label}, {"_id": 0})
        other = str((raced or {}).get("sales_master_id") or "").strip()
        return public_alias(raced) if other == mid else None


async def list_alias_labels_map(db, master_ids: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """sales_master_id → [exact_label, ...]."""
    query: Dict[str, Any] = {}
    if master_ids is not None:
        ids = [str(x).strip() for x in master_ids if str(x or "").strip()]
        if not ids:
            return {}
        query["sales_master_id"] = {"$in": ids}
    rows = await db[COLLECTION].find(query, {"_id": 0, "exact_label": 1, "sales_master_id": 1}).to_list(5000)
    out: Dict[str, List[str]] = {}
    for row in rows:
        mid = str(row.get("sales_master_id") or "").strip()
        label = exact_sales_label(row.get("exact_label"))
        if not mid or not label:
            continue
        out.setdefault(mid, []).append(label)
    for mid in out:
        out[mid] = sorted(set(out[mid]))
    return out
