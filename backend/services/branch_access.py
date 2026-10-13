"""Reusable branch / warehouse access authority.

User model: users may carry optional ``branch_ids`` (list of branch document ids).
There was previously no user↔branch field — this is the smallest additive model.

Scopes:
- ALL: local admin / local unrestricted / production HOLDING_TEAM
- ASSIGNED: explicit ``branch_ids``
- YGY_FALLBACK: no assignment yet — effective YGY for future cutover (explicit,
  migration-safe; does not mutate the user document)

Backend authorization is authoritative. Frontend filters are convenience only.

Enterprise limitation (this Purchasing/Inventory recovery scope):
Branch authorization here covers Purchasing stock mutations + Inventory/R2/HPP
display scope (physical stock, GR location, purchase return location, Stock Saat
Ini, Stock Opname, Nilai Persediaan qty, HPP Pembelian stock context, Cabang &
Gudang). It does NOT claim full Sales / Production / Delivery / Payroll
enterprise branch isolation.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set

from fastapi import HTTPException

from services.purchasing_inventory_access import (
    HOLDING_TEAM_ROLE,
    is_local_runtime,
)

BRANCHES_COLLECTION = "branches"
WAREHOUSES_COLLECTION = "warehouses"

DEFAULT_BRANCH_CODE = "YGY"
DEFAULT_WAREHOUSE_CODE = "YGY-UTAMA"

SCOPE_ALL = "ALL"
SCOPE_ASSIGNED = "ASSIGNED"
SCOPE_YGY_FALLBACK = "YGY_FALLBACK"

CROSS_BRANCH_DENIED = "Akses cabang ditolak."
CROSS_WAREHOUSE_DENIED = "Akses gudang ditolak."


def _env_pair() -> tuple[Optional[str], Optional[str]]:
    return os.environ.get("APP_ENV"), os.environ.get("MONGO_URL")


def _role(user: Optional[dict]) -> str:
    return str((user or {}).get("role") or "").strip().lower()


def _normalize_ids(raw: Any) -> List[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        value = raw.strip()
        return [value] if value else []
    if isinstance(raw, (list, tuple, set)):
        out: List[str] = []
        seen: Set[str] = set()
        for item in raw:
            value = str(item or "").strip()
            if value and value not in seen:
                seen.add(value)
                out.append(value)
        return out
    value = str(raw).strip()
    return [value] if value else []


def assigned_branch_ids(user: Optional[dict]) -> List[str]:
    """Explicit assignments only. Never invents YGY here."""
    user = user or {}
    ids = _normalize_ids(user.get("branch_ids"))
    if ids:
        return ids
    # Single-id alias if ever written that way
    return _normalize_ids(user.get("branch_id"))


def has_global_branch_scope(
    user: Optional[dict],
    *,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> bool:
    """Local admin / unrestricted local runtime, or production HOLDING_TEAM."""
    if app_env is None and mongo_url is None:
        app_env, mongo_url = _env_pair()
    user = user or {}
    if is_local_runtime(app_env, mongo_url):
        role = _role(user)
        if role in {"admin", "holding_team", "direktur_utama"} or user.get("is_super_admin"):
            return True
        # Local preview: treat as unrestricted for UAT unless explicitly assigned
        # (assigned local users stay scoped so access tests work).
        if not assigned_branch_ids(user):
            return True
        return False
    # Production: Holding (package ops) + Direktur (Owner full-access policy).
    return _role(user) in {HOLDING_TEAM_ROLE, "direktur_utama"}


async def find_branch_by_code(db, code: str = DEFAULT_BRANCH_CODE) -> Optional[dict]:
    code = str(code or "").strip().upper()
    if not code:
        return None
    doc = await db[BRANCHES_COLLECTION].find_one(
        {"code": code, "active": {"$ne": False}},
        {"_id": 0},
    )
    if doc:
        return doc
    # Name fallback for Yogyakarta only
    if code == DEFAULT_BRANCH_CODE:
        return await db[BRANCHES_COLLECTION].find_one(
            {"name": {"$regex": "yogyakarta", "$options": "i"}, "active": {"$ne": False}},
            {"_id": 0},
        )
    return None


async def resolve_branch_access(
    db,
    user: Optional[dict],
    *,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> dict:
    """Return effective branch authority for one user (read-only; no mutation)."""
    if app_env is None and mongo_url is None:
        app_env, mongo_url = _env_pair()
    user = user or {}
    assigned = assigned_branch_ids(user)
    if has_global_branch_scope(user, app_env=app_env, mongo_url=mongo_url):
        return {
            "scope": SCOPE_ALL,
            "branch_ids": None,
            "default_branch_code": DEFAULT_BRANCH_CODE,
            "ygy_fallback_applied": False,
            "holding_team_global": _role(user) == HOLDING_TEAM_ROLE,
            "local_unrestricted": bool(is_local_runtime(app_env, mongo_url)),
        }
    if assigned:
        return {
            "scope": SCOPE_ASSIGNED,
            "branch_ids": assigned,
            "default_branch_code": DEFAULT_BRANCH_CODE,
            "ygy_fallback_applied": False,
            "holding_team_global": False,
            "local_unrestricted": False,
        }
    ygy = await find_branch_by_code(db, DEFAULT_BRANCH_CODE)
    fallback_ids = [ygy["id"]] if ygy and ygy.get("id") else []
    return {
        "scope": SCOPE_YGY_FALLBACK,
        "branch_ids": fallback_ids,
        "default_branch_code": DEFAULT_BRANCH_CODE,
        "ygy_fallback_applied": True,
        "holding_team_global": False,
        "local_unrestricted": False,
        "ygy_branch_id": (ygy or {}).get("id"),
        "ygy_ready": bool(ygy),
    }


def branch_id_allowed(access: dict, branch_id: Optional[str]) -> bool:
    bid = str(branch_id or "").strip()
    if not bid:
        return False
    if access.get("scope") == SCOPE_ALL:
        return True
    allowed = set(access.get("branch_ids") or [])
    return bid in allowed


async def require_branch_access(
    db,
    user: Optional[dict],
    branch_id: Optional[str],
    *,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> dict:
    access = await resolve_branch_access(db, user, app_env=app_env, mongo_url=mongo_url)
    if not branch_id_allowed(access, branch_id):
        raise HTTPException(403, CROSS_BRANCH_DENIED)
    return access


async def require_warehouse_access(
    db,
    user: Optional[dict],
    warehouse_id: Optional[str],
    *,
    expected_branch_id: Optional[str] = None,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> dict:
    wid = str(warehouse_id or "").strip()
    if not wid:
        raise HTTPException(400, "warehouse_id wajib diisi")
    warehouse = await db[WAREHOUSES_COLLECTION].find_one({"id": wid}, {"_id": 0})
    if not warehouse:
        raise HTTPException(404, "Gudang tidak ditemukan")
    branch_id = str(warehouse.get("branch_id") or "").strip()
    if expected_branch_id and str(expected_branch_id).strip() != branch_id:
        # Payload mismatch (branch/warehouse pair), not an authorization miss.
        raise HTTPException(400, "Gudang tidak berada di cabang yang dipilih")
    access = await require_branch_access(
        db, user, branch_id, app_env=app_env, mongo_url=mongo_url,
    )
    return {"access": access, "warehouse": warehouse, "branch_id": branch_id}


async def filter_branches_for_user(
    db,
    user: Optional[dict],
    branches: Sequence[dict],
    *,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> List[dict]:
    access = await resolve_branch_access(db, user, app_env=app_env, mongo_url=mongo_url)
    if access.get("scope") == SCOPE_ALL:
        return list(branches)
    allowed = set(access.get("branch_ids") or [])
    return [row for row in branches if str(row.get("id") or "") in allowed]


async def filter_warehouses_for_user(
    db,
    user: Optional[dict],
    warehouses: Sequence[dict],
    *,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> List[dict]:
    access = await resolve_branch_access(db, user, app_env=app_env, mongo_url=mongo_url)
    if access.get("scope") == SCOPE_ALL:
        return list(warehouses)
    allowed = set(access.get("branch_ids") or [])
    return [row for row in warehouses if str(row.get("branch_id") or "") in allowed]


async def authorize_stock_location(
    db,
    user: Optional[dict],
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    require_warehouse: bool = False,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> dict:
    """Authorize optional branch/warehouse query or mutation payload.

    If warehouse_id is set, warehouse must belong to an authorized branch.
    If only branch_id is set, that branch must be authorized.
    If neither is set and require_warehouse is False, resolve default YGY catalog branch.
    """
    wid = str(warehouse_id or "").strip()
    bid = str(branch_id or "").strip()
    if wid:
        return await require_warehouse_access(
            db,
            user,
            wid,
            expected_branch_id=bid or None,
            app_env=app_env,
            mongo_url=mongo_url,
        )
    if bid:
        access = await require_branch_access(
            db, user, bid, app_env=app_env, mongo_url=mongo_url,
        )
        return {"access": access, "warehouse": None, "branch_id": bid}
    if require_warehouse:
        raise HTTPException(400, "warehouse_id wajib diisi")
    resolved = await resolve_stock_catalog_branch_id(
        db, user, None, app_env=app_env, mongo_url=mongo_url,
    )
    return {
        "access": resolved.get("access"),
        "warehouse": None,
        "branch_id": (resolved.get("branch") or {}).get("id"),
        "branch": resolved.get("branch"),
        "defaulted": True,
    }


def can_switch_branches(access: Optional[dict]) -> bool:
    """True when UI may show a multi-branch selector (HOLDING / local unrestricted)."""
    return str((access or {}).get("scope") or "") == SCOPE_ALL


async def resolve_stock_catalog_branch_id(
    db,
    user: Optional[dict],
    requested_branch_id: Optional[str] = None,
    *,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> dict:
    """Pick one branch for Stock Saat Ini. Never aggregates all branches.

    Default = YGY when request omits branch_id.
    Explicit request must be authorized.
    """
    access = await resolve_branch_access(db, user, app_env=app_env, mongo_url=mongo_url)
    requested = str(requested_branch_id or "").strip()
    if requested:
        if not branch_id_allowed(access, requested):
            raise HTTPException(403, CROSS_BRANCH_DENIED)
        branch = await db[BRANCHES_COLLECTION].find_one({"id": requested}, {"_id": 0})
        if not branch:
            raise HTTPException(404, "Cabang tidak ditemukan")
        return {"branch": branch, "access": access, "defaulted": False}

    ygy = await find_branch_by_code(db, DEFAULT_BRANCH_CODE)
    if not ygy:
        # No YGY: prefer the sole active branch so Stock Saat Ini / adjustment
        # work on isolated UAT (and other single-branch) databases. Never sum
        # all branches — multi-branch without YGY still requires an explicit pick.
        if access.get("scope") == SCOPE_ALL:
            active = await db[BRANCHES_COLLECTION].find(
                {"active": {"$ne": False}},
                {"_id": 0},
            ).sort([("code", 1), ("id", 1)]).to_list(500)
            active = [row for row in active if str(row.get("id") or "").strip()]
            if len(active) == 1:
                return {"branch": active[0], "access": access, "defaulted": True}
            return {"branch": None, "access": access, "defaulted": True}
        raise HTTPException(409, "Cabang operasional Yogyakarta belum dikonfigurasi")
    if not branch_id_allowed(access, ygy.get("id")):
        # Assigned to non-YGY only — use first assigned branch as default
        allowed = list(access.get("branch_ids") or [])
        if not allowed:
            raise HTTPException(403, CROSS_BRANCH_DENIED)
        branch = await db[BRANCHES_COLLECTION].find_one({"id": allowed[0]}, {"_id": 0})
        if not branch:
            raise HTTPException(403, CROSS_BRANCH_DENIED)
        return {"branch": branch, "access": access, "defaulted": True}
    return {"branch": ygy, "access": access, "defaulted": True}
