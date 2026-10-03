"""Branch and Warehouse organizational masters.

Stable ids for future inventory identity (master_item_id + branch_id + warehouse_id).
Creating a Branch never creates a Warehouse. Soft deactivate via active flag only.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

BRANCHES_COLLECTION = "branches"
WAREHOUSES_COLLECTION = "warehouses"

PERM_BRANCH_VIEW = "org.branches.view"
PERM_BRANCH_MANAGE = "org.branches.manage"
PERM_WAREHOUSE_VIEW = "org.warehouses.view"
PERM_WAREHOUSE_MANAGE = "org.warehouses.manage"


class OrgLocationValidationError(ValueError):
    """Invalid payload for branch/warehouse."""


class OrgLocationConflictError(Exception):
    """Duplicate code or uniqueness violation."""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def actor_label(user: Optional[dict]) -> str:
    if not user:
        return ""
    return str(user.get("email") or user.get("name") or user.get("id") or "").strip()


def normalize_code(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip().upper()


def normalize_name(raw: Any) -> str:
    if raw is None:
        return ""
    return str(raw).strip()


def normalize_active(raw: Any, *, default: bool = True) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return bool(raw)
    s = str(raw).strip().lower()
    if s in ("1", "true", "yes", "y", "aktif", "active"):
        return True
    if s in ("0", "false", "no", "n", "nonaktif", "inactive"):
        return False
    raise OrgLocationValidationError("Nilai active tidak valid")


def public_branch(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "code": normalize_code(doc.get("code")),
        "name": normalize_name(doc.get("name")),
        "active": bool(doc.get("active", True)),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
        "updated_by": doc.get("updated_by"),
    }


def public_warehouse(doc: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "branch_id": str(doc.get("branch_id") or ""),
        "code": normalize_code(doc.get("code")),
        "name": normalize_name(doc.get("name")),
        "active": bool(doc.get("active", True)),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "created_by": doc.get("created_by"),
        "updated_by": doc.get("updated_by"),
    }


def validate_branch_create(raw: dict) -> Tuple[str, str, bool]:
    code = normalize_code((raw or {}).get("code"))
    name = normalize_name((raw or {}).get("name"))
    if not code:
        raise OrgLocationValidationError("Kode cabang wajib diisi")
    if not name:
        raise OrgLocationValidationError("Nama cabang wajib diisi")
    active = normalize_active((raw or {}).get("active"), default=True) if "active" in (raw or {}) else True
    return code, name, active


def validate_warehouse_create(raw: dict) -> Tuple[str, str, str, bool]:
    branch_id = str((raw or {}).get("branch_id") or "").strip()
    code = normalize_code((raw or {}).get("code"))
    name = normalize_name((raw or {}).get("name"))
    if not branch_id:
        raise OrgLocationValidationError("Cabang wajib diisi")
    if not code:
        raise OrgLocationValidationError("Kode gudang wajib diisi")
    if not name:
        raise OrgLocationValidationError("Nama gudang wajib diisi")
    active = normalize_active((raw or {}).get("active"), default=True) if "active" in (raw or {}) else True
    return branch_id, code, name, active


async def ensure_indexes(db) -> None:
    branches = db[BRANCHES_COLLECTION]
    await branches.create_index("id", unique=True)
    await branches.create_index("code", unique=True)

    warehouses = db[WAREHOUSES_COLLECTION]
    await warehouses.create_index("id", unique=True)
    await warehouses.create_index("branch_id")
    await warehouses.create_index([("branch_id", 1), ("code", 1)], unique=True)


async def list_branches(db, *, q: Optional[str] = None, active: Optional[bool] = None) -> List[dict]:
    query: Dict[str, Any] = {}
    if active is not None:
        query["active"] = bool(active)
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"code": {"$regex": needle, "$options": "i"}},
            {"name": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[BRANCHES_COLLECTION].find(query, {"_id": 0}).sort("code", 1).to_list(5000)
    return [public_branch(d) for d in docs]


async def get_branch(db, branch_id: str) -> Optional[dict]:
    doc = await db[BRANCHES_COLLECTION].find_one({"id": str(branch_id)}, {"_id": 0})
    return public_branch(doc)


async def create_branch(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    code, name, active = validate_branch_create(raw)
    existing = await db[BRANCHES_COLLECTION].find_one({"code": code}, {"_id": 0, "id": 1})
    if existing:
        raise OrgLocationConflictError(f"Kode cabang '{code}' sudah digunakan")
    ts = now_iso()
    actor = actor_label(user)
    doc = {
        "id": str(uuid.uuid4()),
        "code": code,
        "name": name,
        "active": active,
        "created_at": ts,
        "updated_at": ts,
        "created_by": actor,
        "updated_by": actor,
    }
    try:
        await db[BRANCHES_COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise OrgLocationConflictError(f"Kode cabang '{code}' sudah digunakan") from exc
    return public_branch(doc)


async def update_branch(db, branch_id: str, raw: dict, *, user: Optional[dict] = None) -> dict:
    existing = await db[BRANCHES_COLLECTION].find_one({"id": str(branch_id)}, {"_id": 0})
    if not existing:
        raise KeyError(branch_id)

    updates: Dict[str, Any] = {}
    payload = raw or {}
    if "code" in payload and payload.get("code") is not None:
        code = normalize_code(payload.get("code"))
        if not code:
            raise OrgLocationValidationError("Kode cabang wajib diisi")
        conflict = await db[BRANCHES_COLLECTION].find_one(
            {"code": code, "id": {"$ne": str(branch_id)}},
            {"_id": 0, "id": 1},
        )
        if conflict:
            raise OrgLocationConflictError(f"Kode cabang '{code}' sudah digunakan")
        updates["code"] = code
    if "name" in payload and payload.get("name") is not None:
        name = normalize_name(payload.get("name"))
        if not name:
            raise OrgLocationValidationError("Nama cabang wajib diisi")
        updates["name"] = name
    if "active" in payload:
        updates["active"] = normalize_active(payload.get("active"), default=True)

    if not updates:
        return public_branch(existing)

    updates["updated_at"] = now_iso()
    updates["updated_by"] = actor_label(user)
    try:
        await db[BRANCHES_COLLECTION].update_one({"id": str(branch_id)}, {"$set": updates})
    except DuplicateKeyError as exc:
        raise OrgLocationConflictError("Kode cabang sudah digunakan") from exc
    doc = await db[BRANCHES_COLLECTION].find_one({"id": str(branch_id)}, {"_id": 0})
    return public_branch(doc)


async def list_warehouses(
    db,
    *,
    branch_id: Optional[str] = None,
    q: Optional[str] = None,
    active: Optional[bool] = None,
) -> List[dict]:
    query: Dict[str, Any] = {}
    bid = str(branch_id or "").strip()
    if bid:
        query["branch_id"] = bid
    if active is not None:
        query["active"] = bool(active)
    needle = (q or "").strip()
    if needle:
        query["$or"] = [
            {"code": {"$regex": needle, "$options": "i"}},
            {"name": {"$regex": needle, "$options": "i"}},
        ]
    docs = await db[WAREHOUSES_COLLECTION].find(query, {"_id": 0}).sort("code", 1).to_list(5000)
    return [public_warehouse(d) for d in docs]


async def get_warehouse(db, warehouse_id: str) -> Optional[dict]:
    doc = await db[WAREHOUSES_COLLECTION].find_one({"id": str(warehouse_id)}, {"_id": 0})
    return public_warehouse(doc)


async def create_warehouse(db, raw: dict, *, user: Optional[dict] = None) -> dict:
    branch_id, code, name, active = validate_warehouse_create(raw)
    branch = await db[BRANCHES_COLLECTION].find_one({"id": branch_id}, {"_id": 0, "id": 1})
    if not branch:
        raise OrgLocationValidationError("Cabang tidak ditemukan")
    conflict = await db[WAREHOUSES_COLLECTION].find_one(
        {"branch_id": branch_id, "code": code},
        {"_id": 0, "id": 1},
    )
    if conflict:
        raise OrgLocationConflictError(f"Kode gudang '{code}' sudah digunakan pada cabang ini")

    ts = now_iso()
    actor = actor_label(user)
    doc = {
        "id": str(uuid.uuid4()),
        "branch_id": branch_id,
        "code": code,
        "name": name,
        "active": active,
        "created_at": ts,
        "updated_at": ts,
        "created_by": actor,
        "updated_by": actor,
    }
    try:
        await db[WAREHOUSES_COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError as exc:
        raise OrgLocationConflictError(f"Kode gudang '{code}' sudah digunakan pada cabang ini") from exc
    return public_warehouse(doc)


async def update_warehouse(db, warehouse_id: str, raw: dict, *, user: Optional[dict] = None) -> dict:
    existing = await db[WAREHOUSES_COLLECTION].find_one({"id": str(warehouse_id)}, {"_id": 0})
    if not existing:
        raise KeyError(warehouse_id)

    updates: Dict[str, Any] = {}
    payload = raw or {}
    branch_id = str(existing.get("branch_id") or "")

    if "branch_id" in payload and payload.get("branch_id") is not None:
        new_branch = str(payload.get("branch_id") or "").strip()
        if not new_branch:
            raise OrgLocationValidationError("Cabang wajib diisi")
        branch = await db[BRANCHES_COLLECTION].find_one({"id": new_branch}, {"_id": 0, "id": 1})
        if not branch:
            raise OrgLocationValidationError("Cabang tidak ditemukan")
        branch_id = new_branch
        updates["branch_id"] = new_branch

    next_code = normalize_code(existing.get("code"))
    if "code" in payload and payload.get("code") is not None:
        next_code = normalize_code(payload.get("code"))
        if not next_code:
            raise OrgLocationValidationError("Kode gudang wajib diisi")
        updates["code"] = next_code

    if "name" in payload and payload.get("name") is not None:
        name = normalize_name(payload.get("name"))
        if not name:
            raise OrgLocationValidationError("Nama gudang wajib diisi")
        updates["name"] = name

    if "active" in payload:
        updates["active"] = normalize_active(payload.get("active"), default=True)

    if "code" in updates or "branch_id" in updates:
        conflict = await db[WAREHOUSES_COLLECTION].find_one(
            {
                "branch_id": branch_id,
                "code": next_code,
                "id": {"$ne": str(warehouse_id)},
            },
            {"_id": 0, "id": 1},
        )
        if conflict:
            raise OrgLocationConflictError(f"Kode gudang '{next_code}' sudah digunakan pada cabang ini")

    if not updates:
        return public_warehouse(existing)

    updates["updated_at"] = now_iso()
    updates["updated_by"] = actor_label(user)
    try:
        await db[WAREHOUSES_COLLECTION].update_one({"id": str(warehouse_id)}, {"$set": updates})
    except DuplicateKeyError as exc:
        raise OrgLocationConflictError("Kode gudang sudah digunakan pada cabang ini") from exc
    doc = await db[WAREHOUSES_COLLECTION].find_one({"id": str(warehouse_id)}, {"_id": 0})
    return public_warehouse(doc)
