"""One-time cutover for legacy package grants.

Computes access that was already effective, removes stored package keys that
were not effective, and marks the document normalized. After this marker,
a checked permission saved from the editor is effective without a second
approval. The marker is not an approval list.

Holding and Direktur keep every package key already in the grant, because
those keys were already effective. Admin and Super Admin bypass is untouched.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List

from services.purchasing_inventory_access import (
    DIRECTOR_FULL_ACCESS_ROLE,
    HOLDING_TEAM_ROLE,
    MANUAL_GRANT_NORMALIZED_FIELD,
    PACKAGE_CONFIRMED_KEYS_FIELD,
    PACKAGE_PERMISSIONS,
    legacy_effective_package_keys,
)

UNRESTRICTED_PACKAGE_ROLES = frozenset({HOLDING_TEAM_ROLE, DIRECTOR_FULL_ACCESS_ROLE})


def _role_name(doc: Dict[str, Any]) -> str:
    return str((doc or {}).get("role") or "").strip().lower()


def _unrestricted(role: str) -> bool:
    return role in UNRESTRICTED_PACKAGE_ROLES


def plan_role_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    role = _role_name(doc)
    stored = list((doc or {}).get("permissions") or [])
    if (doc or {}).get(MANUAL_GRANT_NORMALIZED_FIELD):
        kept = sorted(PACKAGE_PERMISSIONS.intersection(stored))
        return {
            "kind": "role",
            "id": role,
            "action": "skip",
            "reason": "already_normalized",
            "removed_package_keys": [],
            "kept_package_keys": kept,
            "next_permissions": list(stored),
            "next_confirmed_keys": list((doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD) or kept),
        }
    kept_set = legacy_effective_package_keys(
        stored,
        (doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD),
        unrestricted=_unrestricted(role),
        already_normalized=False,
    )
    removed = PACKAGE_PERMISSIONS.intersection(stored) - kept_set
    return {
        "kind": "role",
        "id": role,
        "action": "normalize",
        "reason": "legacy_grant",
        "removed_package_keys": sorted(removed),
        "kept_package_keys": sorted(kept_set),
        "next_permissions": sorted(set(stored) - removed),
        "next_confirmed_keys": sorted(kept_set),
    }


def plan_user_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    identity = (doc or {}).get("email") or (doc or {}).get("id") or ""
    override = dict((doc or {}).get("permissions_override") or {})
    role = _role_name(doc)
    if (doc or {}).get(MANUAL_GRANT_NORMALIZED_FIELD):
        kept = sorted(
            key for key, allowed in override.items()
            if allowed is True and key in PACKAGE_PERMISSIONS
        )
        return {
            "kind": "user",
            "id": identity,
            "role": role,
            "action": "skip",
            "reason": "already_normalized",
            "removed_package_keys": [],
            "kept_package_keys": kept,
            "next_override": override,
            "next_confirmed_keys": list((doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD) or kept),
        }
    true_keys = [key for key, allowed in override.items() if allowed is True]
    kept_set = legacy_effective_package_keys(
        true_keys,
        (doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD),
        unrestricted=_unrestricted(role),
        already_normalized=False,
    )
    removed = {key for key in true_keys if key in PACKAGE_PERMISSIONS and key not in kept_set}
    next_override = dict(override)
    for key in removed:
        next_override.pop(key, None)
    return {
        "kind": "user",
        "id": identity,
        "role": role,
        "action": "normalize",
        "reason": "legacy_grant",
        "removed_package_keys": sorted(removed),
        "kept_package_keys": sorted(kept_set),
        "next_override": next_override,
        "next_confirmed_keys": sorted(kept_set),
    }


def summarize_plans(plans: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(plans)
    removed: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("removed_package_keys"):
            removed.append({
                "kind": row.get("kind"),
                "id": row.get("id"),
                "removed_package_keys": row.get("removed_package_keys"),
                "kept_package_keys": row.get("kept_package_keys"),
            })
    return {
        "examined": len(rows),
        "normalize": sum(1 for row in rows if row.get("action") == "normalize"),
        "skip": sum(1 for row in rows if row.get("action") == "skip"),
        "roles_with_removals": sum(1 for row in removed if row["kind"] == "role"),
        "users_with_removals": sum(1 for row in removed if row["kind"] == "user"),
        "removals": removed,
    }
