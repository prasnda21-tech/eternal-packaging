"""Central Master Sales catalog (operational identity).

sales_name is the Accurate label (exact). sales_user_mapping remains the
Accurate-label → users.id bridge. Master Sales does not rewrite historical
orders/invoices/CRM rows on rename.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from fastapi import HTTPException

from pymongo import ReturnDocument

from services.branch_access import SCOPE_ALL, resolve_branch_access
from services.org_locations import BRANCHES_COLLECTION
from services.sales_user_mapping import (
    MAPPING_TARGET_ROLES,
    exact_sales_label,
    load_mapping_doc,
    mapping_dict_from_entries,
    save_mapping,
)

COLLECTION = "sales_master"
AUDIT_COLLECTION = "audit_logs"

PERM_VIEW = "settings.sales_master.view"
PERM_MANAGE = "settings.sales_master.manage"

_WS_RE = re.compile(r"\s+")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def actor_id(user: Optional[dict]) -> str:
    user = user or {}
    return str(user.get("user_id") or user.get("id") or user.get("sub") or "").strip()


def actor_label(user: Optional[dict]) -> str:
    user = user or {}
    return str(user.get("email") or user.get("name") or actor_id(user) or "").strip()


def normalize_sales_name(raw: Any) -> str:
    """Display/storage form: strip + collapse internal whitespace. Preserves case."""
    text = str(raw or "").strip()
    if not text:
        return ""
    return _WS_RE.sub(" ", text)


def name_key(raw: Any) -> str:
    """Uniqueness key: case-insensitive normalized name."""
    return normalize_sales_name(raw).casefold()


def public_sales(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "sales_name": doc.get("sales_name") or "",
        "user_id": doc.get("user_id") or None,
        "branch_ids": list(doc.get("branch_ids") or []),
        "active": bool(doc.get("active", True)),
        "sort_order": int(doc.get("sort_order") or 0),
        "doc_version": int(doc.get("doc_version") or 1),
        "created_at": doc.get("created_at"),
        "created_by": doc.get("created_by"),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "updated_by_name": doc.get("updated_by_name"),
    }


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("name_key", unique=True)
    await col.create_index([("active", 1), ("sort_order", 1), ("sales_name", 1)])
    await col.create_index("branch_ids")


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


async def _validate_branch_ids(db, branch_ids: Sequence[str]) -> List[str]:
    clean: List[str] = []
    seen: Set[str] = set()
    for raw in branch_ids or []:
        bid = str(raw or "").strip()
        if not bid or bid in seen:
            continue
        seen.add(bid)
        clean.append(bid)
    if not clean:
        return []
    rows = await db[BRANCHES_COLLECTION].find(
        {"id": {"$in": clean}},
        {"_id": 0, "id": 1, "active": 1},
    ).to_list(len(clean))
    found = {str(r.get("id")): r for r in rows}
    missing = [b for b in clean if b not in found]
    if missing:
        raise HTTPException(400, f"Cabang tidak ditemukan: {', '.join(missing)}")
    inactive = [b for b in clean if found[b].get("active") is False]
    if inactive:
        raise HTTPException(400, f"Cabang nonaktif tidak dapat ditugaskan: {', '.join(inactive)}")
    return clean


async def _validate_user_id(db, user_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return (user_id, role). Mapping sync only when role is a mapping target."""
    uid = str(user_id or "").strip()
    if not uid:
        return None, None
    row = await db.users.find_one({"id": uid}, {"_id": 0, "id": 1, "status": 1, "role": 1})
    if not row:
        raise HTTPException(400, "Akun Eternal tidak ditemukan")
    if str(row.get("status") or "active") == "inactive":
        raise HTTPException(400, "Akun Eternal nonaktif tidak dapat dihubungkan")
    return uid, exact_sales_label(row.get("role"))


async def _assert_unique_name(db, name: str, *, exclude_id: str = "") -> None:
    key = name_key(name)
    if not key:
        raise HTTPException(400, "Nama Sales wajib diisi")
    query: Dict[str, Any] = {"name_key": key}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    existing = await db[COLLECTION].find_one(query, {"_id": 0, "id": 1, "sales_name": 1})
    if existing:
        raise HTTPException(409, f"Nama Sales sudah dipakai: {existing.get('sales_name')}")


def _catalog_branch_filter(access: dict) -> Optional[Dict[str, Any]]:
    """None = no branch restriction. Dict = Mongo filter fragment."""
    if access.get("scope") == SCOPE_ALL:
        return None
    allowed = list(access.get("branch_ids") or [])
    return {
        "$or": [
            {"branch_ids": {"$exists": False}},
            {"branch_ids": []},
            {"branch_ids": None},
            {"branch_ids": {"$in": allowed}},
        ]
    }


async def list_sales_master(
    db,
    *,
    user: Optional[dict] = None,
    active_only: bool = False,
    q: str = "",
    for_catalog: bool = False,
) -> List[dict]:
    query: Dict[str, Any] = {}
    if active_only:
        query["active"] = True
    needle = normalize_sales_name(q)
    if needle:
        query["sales_name"] = {"$regex": re.escape(needle), "$options": "i"}
    if for_catalog and user is not None:
        access = await resolve_branch_access(db, user)
        frag = _catalog_branch_filter(access)
        if frag:
            query = {"$and": [query, frag]} if query else frag
    rows = await db[COLLECTION].find(query, {"_id": 0}).sort([
        ("sort_order", 1),
        ("sales_name", 1),
    ]).to_list(2000)
    return [public_sales(r) for r in rows if r]


async def get_sales_master(db, sales_id: str) -> Optional[dict]:
    sid = str(sales_id or "").strip()
    if not sid:
        return None
    doc = await db[COLLECTION].find_one({"id": sid}, {"_id": 0})
    return public_sales(doc)


async def require_active_sales(db, sales_id: str, *, user: Optional[dict] = None) -> dict:
    pub = await get_sales_master(db, sales_id)
    if not pub:
        raise HTTPException(400, "Sales tidak ditemukan di Master Sales")
    if not pub.get("active"):
        raise HTTPException(400, "Sales nonaktif tidak dapat dipilih untuk transaksi baru")
    if user is not None:
        allowed = await list_sales_master(db, user=user, active_only=True, for_catalog=True)
        if not any(r["id"] == pub["id"] for r in allowed):
            raise HTTPException(403, "Sales tidak tersedia untuk cabang Anda")
    return pub


async def _assert_mapping_rename_safe(
    db,
    *,
    old_name: str,
    new_name: str,
    user_id: Optional[str],
    user_role: Optional[str],
) -> None:
    """Raise 409 if rename would collide with mapping to a different Eternal user."""
    mapping_uid = user_id if (user_id and user_role in MAPPING_TARGET_ROLES) else None
    old_label = exact_sales_label(old_name)
    new_label = exact_sales_label(new_name)
    if not new_label or old_label == new_label:
        return
    doc = await load_mapping_doc(db)
    for row in doc.get("entries") or []:
        if not isinstance(row, dict):
            continue
        label = exact_sales_label(row.get("label"))
        uid = str(row.get("user_id") or "").strip()
        if label == new_label and mapping_uid and uid and uid != mapping_uid:
            raise HTTPException(
                409,
                "Rename/label bentrok dengan mapping Accurate existing ke user lain. "
                "Perbarui Mapping Sales Accurate terlebih dahulu.",
            )
        if label == new_label and not mapping_uid and uid:
            raise HTTPException(
                409,
                "Rename/label bentrok dengan mapping Accurate existing. "
                "Perbarui Mapping Sales Accurate terlebih dahulu.",
            )


async def _sync_mapping_label(
    db,
    *,
    old_name: str,
    new_name: str,
    user_id: Optional[str],
    user_role: Optional[str],
    actor: dict,
) -> None:
    """Keep Accurate mapping aligned when Master sales_name (Accurate label) changes.

    Mapping entries only accept sales_eternal users (existing contract). Links to
    other roles stay on Master Sales only.
    """
    mapping_uid = user_id if (user_id and user_role in MAPPING_TARGET_ROLES) else None
    old_label = exact_sales_label(old_name)
    new_label = exact_sales_label(new_name)
    doc = await load_mapping_doc(db)
    entries = list(doc.get("entries") or [])
    changed = False
    next_entries: List[dict] = []
    for row in entries:
        if not isinstance(row, dict):
            continue
        label = exact_sales_label(row.get("label"))
        uid = str(row.get("user_id") or "").strip()
        if label == old_label and old_label != new_label:
            if new_label:
                next_entries.append({"label": new_label, "user_id": mapping_uid or uid or None})
            changed = True
            continue
        if label == new_label and mapping_uid and uid and uid != mapping_uid:
            raise HTTPException(
                409,
                "Rename/label bentrok dengan mapping Accurate existing ke user lain. "
                "Perbarui Mapping Sales Accurate terlebih dahulu.",
            )
        next_entries.append({"label": label, "user_id": uid or None})

    if mapping_uid and new_label:
        if not any(exact_sales_label(e.get("label")) == new_label for e in next_entries):
            next_entries.append({"label": new_label, "user_id": mapping_uid})
            changed = True
        else:
            for e in next_entries:
                if exact_sales_label(e.get("label")) == new_label and str(e.get("user_id") or "") != mapping_uid:
                    e["user_id"] = mapping_uid
                    changed = True

    if changed:
        await save_mapping(db, {"entries": next_entries}, actor)


async def create_sales_master(db, payload: dict, actor: dict) -> dict:
    name = normalize_sales_name(payload.get("sales_name") or payload.get("name"))
    if not name:
        raise HTTPException(400, "Nama Sales wajib diisi")
    await _assert_unique_name(db, name)
    branch_ids = await _validate_branch_ids(db, payload.get("branch_ids") or [])
    user_id, user_role = await _validate_user_id(db, payload.get("user_id"))
    sort_order = payload.get("sort_order")
    try:
        sort_order_i = int(sort_order) if sort_order is not None else 0
    except (TypeError, ValueError):
        raise HTTPException(400, "sort_order tidak valid")
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "sales_name": name,
        "name_key": name_key(name),
        "user_id": user_id,
        "branch_ids": branch_ids,
        "active": True if payload.get("active") is None else bool(payload.get("active")),
        "sort_order": sort_order_i,
        "doc_version": 1,
        "created_at": now,
        "created_by": actor_label(actor),
        "updated_at": now,
        "updated_by": actor_label(actor),
        "updated_by_name": (actor or {}).get("name"),
    }
    await db[COLLECTION].insert_one(doc)
    await _sync_mapping_label(
        db, old_name=name, new_name=name, user_id=user_id, user_role=user_role, actor=actor,
    )
    pub = public_sales(doc)
    await _write_audit(db, action="settings.sales_master.create", actor=actor, after=pub, note=f"create {doc['id']}")
    return pub


async def update_sales_master(db, sales_id: str, payload: dict, actor: dict) -> dict:
    sid = str(sales_id or "").strip()
    raw = await db[COLLECTION].find_one({"id": sid}, {"_id": 0})
    if not raw:
        raise HTTPException(404, "Master Sales tidak ditemukan")
    expected = payload.get("doc_version")
    if expected is None:
        raise HTTPException(400, "doc_version wajib diisi")
    try:
        expected_i = int(expected)
    except (TypeError, ValueError):
        raise HTTPException(400, "doc_version tidak valid")
    if int(raw.get("doc_version") or 1) != expected_i:
        raise HTTPException(409, "Data sudah diubah pengelola lain. Muat ulang lalu simpan lagi.")

    before = public_sales(raw)
    name = normalize_sales_name(payload.get("sales_name") if "sales_name" in payload else raw.get("sales_name"))
    if not name:
        raise HTTPException(400, "Nama Sales wajib diisi")
    await _assert_unique_name(db, name, exclude_id=sid)

    branch_ids = raw.get("branch_ids") or []
    if "branch_ids" in payload:
        branch_ids = await _validate_branch_ids(db, payload.get("branch_ids") or [])

    user_id = raw.get("user_id")
    user_role = None
    if "user_id" in payload:
        user_id, user_role = await _validate_user_id(db, payload.get("user_id"))
    elif user_id:
        _uid, user_role = await _validate_user_id(db, user_id)

    active = bool(raw.get("active", True))
    if "active" in payload:
        active = bool(payload.get("active"))

    sort_order = int(raw.get("sort_order") or 0)
    if "sort_order" in payload:
        try:
            sort_order = int(payload.get("sort_order"))
        except (TypeError, ValueError):
            raise HTTPException(400, "sort_order tidak valid")

    old_name = normalize_sales_name(raw.get("sales_name"))
    # Validate mapping rename blockers before write to avoid partial updates.
    if old_name != name or user_id != (before or {}).get("user_id"):
        await _assert_mapping_rename_safe(
            db, old_name=old_name, new_name=name, user_id=user_id, user_role=user_role,
        )

    updates = {
        "sales_name": name,
        "name_key": name_key(name),
        "user_id": user_id,
        "branch_ids": branch_ids,
        "active": active,
        "sort_order": sort_order,
        "updated_at": now_iso(),
        "updated_by": actor_label(actor),
        "updated_by_name": (actor or {}).get("name"),
    }
    result = await db[COLLECTION].find_one_and_update(
        {"id": sid, "doc_version": expected_i},
        {"$set": updates, "$inc": {"doc_version": 1}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    if not result:
        raise HTTPException(409, "Data sudah diubah pengelola lain. Muat ulang lalu simpan lagi.")

    if old_name != name or user_id != (before or {}).get("user_id"):
        await _sync_mapping_label(
            db,
            old_name=old_name,
            new_name=name,
            user_id=user_id,
            user_role=user_role,
            actor=actor,
        )

    pub = public_sales(result)
    await _write_audit(
        db,
        action="settings.sales_master.update",
        actor=actor,
        before=before,
        after=pub,
        note=f"update {sid}",
    )
    return pub


async def resolve_for_po_write(
    db,
    *,
    sales_master_id: Optional[str],
    sales_label: Optional[str],
    user: Optional[dict],
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Return (sales_master_id, orders.sales label, sales_user_id hint).

    Prefer master id when provided. Legacy free-text label still allowed for
    Accurate import / unmatched paths (no auto-create master).
    """
    mid = str(sales_master_id or "").strip()
    if mid:
        master = await require_active_sales(db, mid, user=user)
        label = exact_sales_label(master.get("sales_name"))
        uid = str(master.get("user_id") or "").strip() or None
        if not uid and label:
            mapping = mapping_dict_from_entries((await load_mapping_doc(db)).get("entries"))
            uid = str(mapping.get(label) or "").strip() or None
        return master["id"], label or None, uid
    label = exact_sales_label(sales_label)
    if not label:
        return None, None, None
    # Optional soft-resolve: if label matches an active master, attach id
    key = name_key(label)
    doc = await db[COLLECTION].find_one({"name_key": key, "active": True}, {"_id": 0})
    if doc:
        pub = public_sales(doc)
        uid = str(pub.get("user_id") or "").strip() or None
        return pub["id"], exact_sales_label(pub.get("sales_name")) or label, uid
    return None, label, None
