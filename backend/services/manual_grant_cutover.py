"""One-time cutover for legacy package grants.

The production baseline (merge-base evaluator) ignores
``package_production_confirmed_keys``. A package key is effective only for
``holding_team`` and ``direktur_utama`` when it is already in the stored
grant. Every other role's stored package keys are removed so they stay
inactive after the new evaluator is activated.

Explicit opt-in keys already stored on the role document, or set True on a
user override, stay. Those keys are already effective for an active role.
The confirmation stamp is reported and ignored. It is not an approval list.

Admin and Super Admin bypass is not modified. Branch fields are not written.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from permissions import OPT_IN_ONLY_PERMISSIONS
from services.purchasing_inventory_access import (
    DIRECTOR_FULL_ACCESS_ROLE,
    HOLDING_TEAM_ROLE,
    MANUAL_GRANT_NORMALIZED_FIELD,
    PACKAGE_CONFIRMED_KEYS_FIELD,
    PACKAGE_PERMISSIONS,
)

UNRESTRICTED_PACKAGE_ROLES = frozenset({HOLDING_TEAM_ROLE, DIRECTOR_FULL_ACCESS_ROLE})
BACKUP_COLLECTION = "manual_grant_cutover_backups"
CUTOVER_ID = "manual_grant_v1"
_SECRET_FIELDS = ("password", "password_hash", "access_token", "refresh_token", "token")


class CutoverConflict(RuntimeError):
    def __init__(self, conflicts: List[Dict[str, Any]], *, wrote: bool = False):
        self.conflicts = conflicts
        self.wrote = wrote
        super().__init__("permission cutover conflict")


def require_explicit_database(apply: bool, database: Optional[str], connected_name: Optional[str]) -> None:
    """Apply writes only when the operator names the exact connected database.

    APP_ENV is intentionally not consulted. Production is allowed when this
    check passes. Do not clear APP_ENV to reach the write path.
    """
    if not apply:
        return
    wanted = str(database or "").strip()
    actual = str(connected_name or "").strip()
    if not wanted:
        raise SystemExit("REFUSED: --apply requires --database matching DB_NAME")
    if not actual or wanted != actual:
        raise SystemExit("REFUSED: --database does not match the connected DB_NAME")


def _role_name(doc: Dict[str, Any]) -> str:
    return str((doc or {}).get("role") or "").strip().lower()


def _unrestricted(role: str) -> bool:
    return role in UNRESTRICTED_PACKAGE_ROLES


def baseline_effective_package_keys(role: str, granted) -> set:
    """Package keys the production baseline evaluator actually allowed.

    Confirmation stamps are not read. Holding and Direktur keep granted
    package keys. Every other role keeps none.
    """
    granted_pkg = set(PACKAGE_PERMISSIONS.intersection(granted or []))
    if _unrestricted(role):
        return granted_pkg
    return set()


def _explicit_opt_in(granted) -> List[str]:
    return sorted(OPT_IN_ONLY_PERMISSIONS.intersection(granted or []))


def _confirmed_present(doc: Dict[str, Any]):
    if PACKAGE_CONFIRMED_KEYS_FIELD in (doc or {}):
        return list((doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD) or [])
    return None


def role_fingerprint(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "permissions": list((doc or {}).get("permissions") or []),
        "confirmed": _confirmed_present(doc),
        "normalized": (doc or {}).get(MANUAL_GRANT_NORMALIZED_FIELD) is True,
    }


def user_fingerprint(doc: Dict[str, Any]) -> Dict[str, Any]:
    override = (doc or {}).get("permissions_override") or {}
    return {
        "override": {key: override[key] for key in override},
        "confirmed": _confirmed_present(doc),
        "normalized": (doc or {}).get(MANUAL_GRANT_NORMALIZED_FIELD) is True,
    }


def _ignored_confirmation(doc: Dict[str, Any]) -> List[str]:
    raw = (doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD) or []
    return sorted(PACKAGE_PERMISSIONS.intersection(raw))


def plan_role_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    role = _role_name(doc)
    stored = list((doc or {}).get("permissions") or [])
    opt_in = _explicit_opt_in(stored)
    if (doc or {}).get(MANUAL_GRANT_NORMALIZED_FIELD):
        kept = sorted(PACKAGE_PERMISSIONS.intersection(stored))
        return {
            "kind": "role",
            "id": role,
            "action": "skip",
            "reason": "already_normalized",
            "removed_package_keys": [],
            "kept_package_keys": kept,
            "kept_opt_in_keys": opt_in,
            "removed_opt_in_keys": [],
            "ignored_confirmation_keys": _ignored_confirmation(doc),
            "next_permissions": list(stored),
            "next_confirmed_keys": list((doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD) or kept),
            "source_fingerprint": role_fingerprint(doc),
        }
    kept_set = baseline_effective_package_keys(role, stored)
    removed = PACKAGE_PERMISSIONS.intersection(stored) - kept_set
    return {
        "kind": "role",
        "id": role,
        "action": "normalize",
        "reason": "legacy_grant",
        "removed_package_keys": sorted(removed),
        "kept_package_keys": sorted(kept_set),
        "kept_opt_in_keys": opt_in,
        "removed_opt_in_keys": [],
        "ignored_confirmation_keys": _ignored_confirmation(doc),
        "next_permissions": sorted(set(stored) - removed),
        "next_confirmed_keys": sorted(kept_set),
        "source_fingerprint": role_fingerprint(doc),
    }


def plan_user_document(doc: Dict[str, Any]) -> Dict[str, Any]:
    identity = str((doc or {}).get("email") or (doc or {}).get("id") or "")
    identity_field = "email" if (doc or {}).get("email") else "id"
    override = dict((doc or {}).get("permissions_override") or {})
    role = _role_name(doc)
    true_keys = [key for key, allowed in override.items() if allowed is True]
    opt_in = _explicit_opt_in(true_keys)
    if not identity:
        return {
            "kind": "user",
            "id": "",
            "identity_field": identity_field,
            "role": role,
            "action": "skip",
            "reason": "missing_identity",
            "removed_package_keys": [],
            "kept_package_keys": [],
            "kept_opt_in_keys": opt_in,
            "removed_opt_in_keys": [],
            "ignored_confirmation_keys": _ignored_confirmation(doc),
            "next_override": override,
            "next_confirmed_keys": [],
            "source_fingerprint": user_fingerprint(doc),
        }
    if (doc or {}).get(MANUAL_GRANT_NORMALIZED_FIELD):
        kept = sorted(key for key in true_keys if key in PACKAGE_PERMISSIONS)
        return {
            "kind": "user",
            "id": identity,
            "identity_field": identity_field,
            "role": role,
            "action": "skip",
            "reason": "already_normalized",
            "removed_package_keys": [],
            "kept_package_keys": kept,
            "kept_opt_in_keys": opt_in,
            "removed_opt_in_keys": [],
            "ignored_confirmation_keys": _ignored_confirmation(doc),
            "next_override": override,
            "next_confirmed_keys": list((doc or {}).get(PACKAGE_CONFIRMED_KEYS_FIELD) or kept),
            "source_fingerprint": user_fingerprint(doc),
        }
    kept_set = baseline_effective_package_keys(role, true_keys)
    removed = {key for key in true_keys if key in PACKAGE_PERMISSIONS and key not in kept_set}
    next_override = {key: value for key, value in override.items() if key not in removed}
    return {
        "kind": "user",
        "id": identity,
        "identity_field": identity_field,
        "role": role,
        "action": "normalize",
        "reason": "legacy_grant",
        "removed_package_keys": sorted(removed),
        "kept_package_keys": sorted(kept_set),
        "kept_opt_in_keys": opt_in,
        "removed_opt_in_keys": [],
        "ignored_confirmation_keys": _ignored_confirmation(doc),
        "next_override": next_override,
        "next_confirmed_keys": sorted(kept_set),
        "source_fingerprint": user_fingerprint(doc),
    }


def summarize_plans(plans: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(plans)
    removed: List[Dict[str, Any]] = []
    kept_rows: List[Dict[str, Any]] = []
    opt_in_rows: List[Dict[str, Any]] = []
    ignored: List[Dict[str, Any]] = []
    for row in rows:
        if row.get("removed_package_keys"):
            removed.append({
                "kind": row.get("kind"),
                "id": row.get("id"),
                "role": row.get("role"),
                "action": row.get("action"),
                "removed_package_keys": row.get("removed_package_keys"),
                "kept_package_keys": row.get("kept_package_keys"),
            })
        elif row.get("kept_package_keys"):
            kept_rows.append({
                "kind": row.get("kind"),
                "id": row.get("id"),
                "role": row.get("role"),
                "action": row.get("action"),
                "kept_package_keys": row.get("kept_package_keys"),
            })
        if row.get("kept_opt_in_keys") or row.get("removed_opt_in_keys"):
            opt_in_rows.append({
                "kind": row.get("kind"),
                "id": row.get("id"),
                "role": row.get("role"),
                "kept_opt_in_keys": row.get("kept_opt_in_keys"),
                "removed_opt_in_keys": row.get("removed_opt_in_keys"),
            })
        if row.get("ignored_confirmation_keys"):
            ignored.append({
                "kind": row.get("kind"),
                "id": row.get("id"),
                "ignored_confirmation_keys": row.get("ignored_confirmation_keys"),
            })
    return {
        "examined": len(rows),
        "normalize": sum(1 for row in rows if row.get("action") == "normalize"),
        "skip": sum(1 for row in rows if row.get("action") == "skip"),
        "roles_with_removals": sum(
            1 for row in rows if row.get("kind") == "role" and row.get("removed_package_keys")
        ),
        "users_with_removals": sum(
            1 for row in rows if row.get("kind") == "user" and row.get("removed_package_keys")
        ),
        "opt_in_kept": sum(len(row.get("kept_opt_in_keys") or []) for row in rows),
        "opt_in_removed": sum(len(row.get("removed_opt_in_keys") or []) for row in rows),
        "removals": removed,
        "kept": kept_rows,
        "opt_in": opt_in_rows,
        "ignored_confirmation": ignored,
    }


def _public_user(doc: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in (doc or {}).items() if key not in _SECRET_FIELDS}


async def _each(cursor) -> List[Dict[str, Any]]:
    rows = []
    async for doc in cursor:
        rows.append(doc)
    return rows


async def load_targets(db) -> tuple:
    roles = await _each(db.role_permissions.find({}, {"_id": 0}))
    users = []
    async for doc in db.users.find({}, {"_id": 0}):
        users.append(_public_user(doc))
    plans = [plan_role_document(doc) for doc in roles]
    plans.extend(plan_user_document(doc) for doc in users)
    return roles, users, plans


def _role_query(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {"role": plan["id"]}


def _user_query(plan: Dict[str, Any]) -> Dict[str, Any]:
    return {plan.get("identity_field") or "email": plan["id"]}


def _grant_filter(kind: str, live: Dict[str, Any]) -> Dict[str, Any]:
    filt: Dict[str, Any] = {}
    if kind == "role":
        filt["permissions"] = list(live.get("permissions") or [])
    else:
        filt["permissions_override"] = live.get("permissions_override") or {}
    if PACKAGE_CONFIRMED_KEYS_FIELD in live:
        filt[PACKAGE_CONFIRMED_KEYS_FIELD] = list(live.get(PACKAGE_CONFIRMED_KEYS_FIELD) or [])
    else:
        filt[PACKAGE_CONFIRMED_KEYS_FIELD] = {"$exists": False}
    if live.get(MANUAL_GRANT_NORMALIZED_FIELD) is True:
        filt[MANUAL_GRANT_NORMALIZED_FIELD] = True
    else:
        filt[MANUAL_GRANT_NORMALIZED_FIELD] = {"$ne": True}
    return filt


async def apply_cutover(db, plans: Iterable[Dict[str, Any]], *, stamp: str) -> Dict[str, Any]:
    """Backup, then conditionally update. A changed document aborts before writes.

    A second apply skips documents that are already normalized, so a manual
    grant saved after the first apply is left in place.
    """
    selected = [plan for plan in plans if plan.get("action") == "normalize" and plan.get("id")]
    conflicts = []
    live_by_key = {}
    for plan in selected:
        collection = db.role_permissions if plan["kind"] == "role" else db.users
        query = _role_query(plan) if plan["kind"] == "role" else _user_query(plan)
        projection = {"_id": 0}
        if plan["kind"] == "user":
            projection.update({field: 0 for field in _SECRET_FIELDS})
        live = await collection.find_one(query, projection)
        fingerprint = role_fingerprint(live or {}) if plan["kind"] == "role" else user_fingerprint(live or {})
        if not live or fingerprint != plan.get("source_fingerprint"):
            conflicts.append({
                "kind": plan["kind"],
                "id": plan["id"],
                "reason": "changed_since_plan" if live else "missing",
            })
            continue
        live_by_key[(plan["kind"], plan["id"])] = live
    if conflicts:
        raise CutoverConflict(conflicts, wrote=False)

    backup_docs = []
    for plan in selected:
        live = live_by_key[(plan["kind"], plan["id"])]
        if plan["kind"] == "role":
            before = {
                "permissions": list(live.get("permissions") or []),
                "confirmed": _confirmed_present(live),
                "normalized": live.get(MANUAL_GRANT_NORMALIZED_FIELD) is True,
            }
            after = {
                "permissions": list(plan["next_permissions"]),
                "confirmed": list(plan["next_confirmed_keys"]),
                "normalized": True,
            }
        else:
            before = {
                "permissions_override": dict(live.get("permissions_override") or {}),
                "confirmed": _confirmed_present(live),
                "normalized": live.get(MANUAL_GRANT_NORMALIZED_FIELD) is True,
            }
            after = {
                "permissions_override": dict(plan["next_override"]),
                "confirmed": list(plan["next_confirmed_keys"]),
                "normalized": True,
            }
        backup_docs.append({
            "cutover": CUTOVER_ID,
            "at": stamp,
            "kind": plan["kind"],
            "doc_id": plan["id"],
            "identity_field": plan.get("identity_field") or "role",
            "before": before,
            "after": after,
        })

    backup = db[BACKUP_COLLECTION]
    if backup_docs:
        inserted = await backup.insert_many(backup_docs)
        stored = await backup.count_documents({"cutover": CUTOVER_ID, "at": stamp})
        if len(inserted.inserted_ids) != len(backup_docs) or stored < len(backup_docs):
            raise CutoverConflict(
                [{"reason": "backup_count_mismatch", "expected": len(backup_docs), "stored": stored}],
                wrote=False,
            )

    applied = []
    for plan in selected:
        live = live_by_key[(plan["kind"], plan["id"])]
        collection = db.role_permissions if plan["kind"] == "role" else db.users
        query = _role_query(plan) if plan["kind"] == "role" else _user_query(plan)
        query.update(_grant_filter(plan["kind"], live))
        if plan["kind"] == "role":
            update = {"permissions": plan["next_permissions"]}
        else:
            update = {"permissions_override": plan["next_override"]}
        update[PACKAGE_CONFIRMED_KEYS_FIELD] = plan["next_confirmed_keys"]
        update[MANUAL_GRANT_NORMALIZED_FIELD] = True
        update["manual_grant_cutover_at"] = stamp
        result = await collection.update_one(query, {"$set": update})
        if result.matched_count != 1:
            raise CutoverConflict(
                [{"kind": plan["kind"], "id": plan["id"], "reason": "changed_since_backup"}],
                wrote=bool(applied),
            )
        applied.append({"kind": plan["kind"], "id": plan["id"]})
    return {"applied": len(applied), "backup_collection": BACKUP_COLLECTION, "at": stamp}


def _live_matches_after(kind: str, live: Dict[str, Any], after: Dict[str, Any]) -> bool:
    if kind == "role":
        current_grant = list(live.get("permissions") or [])
        expected_grant = list(after.get("permissions") or [])
    else:
        current_grant = dict(live.get("permissions_override") or {})
        expected_grant = dict(after.get("permissions_override") or {})
    return (
        current_grant == expected_grant
        and list(live.get(PACKAGE_CONFIRMED_KEYS_FIELD) or []) == list(after.get("confirmed") or [])
        and (live.get(MANUAL_GRANT_NORMALIZED_FIELD) is True) == (after.get("normalized") is True)
        and PACKAGE_CONFIRMED_KEYS_FIELD in live
    )


async def rollback_cutover(db, *, apply: bool) -> Dict[str, Any]:
    """Restore the pre-cutover grant only when the live grant still matches the migration.

    Valid before the new permission evaluator is activated. Restoring a
    pre-image after that evaluator is live can make removed package keys
    effective. A grant edited after the migration is refused with no write.
    """
    rows = await _each(
        db[BACKUP_COLLECTION].find({"cutover": CUTOVER_ID}, {"_id": 0}).sort("at", 1)
    )
    latest: Dict[tuple, Dict[str, Any]] = {}
    for row in rows:
        latest[(row.get("kind"), row.get("doc_id"))] = row
    conflicts = []
    live_by_key = {}
    for key, row in latest.items():
        kind, doc_id = key
        if kind == "role":
            live = await db.role_permissions.find_one({"role": doc_id}, {"_id": 0})
        else:
            field = row.get("identity_field") or "email"
            projection = {"_id": 0, **{name: 0 for name in _SECRET_FIELDS}}
            live = await db.users.find_one({field: doc_id}, projection)
        if not live or not _live_matches_after(kind, live, row.get("after") or {}):
            conflicts.append({"kind": kind, "id": doc_id, "reason": "changed_after_migration"})
            continue
        live_by_key[key] = live
    if conflicts:
        raise CutoverConflict(conflicts, wrote=False)
    restored = []
    for key, row in latest.items():
        kind, doc_id = key
        before = row.get("before") or {}
        live = live_by_key[key]
        if not apply:
            restored.append({"kind": kind, "id": doc_id})
            continue
        unset = {"manual_grant_cutover_at": ""}
        if kind == "role":
            sets = {"permissions": list(before.get("permissions") or [])}
        else:
            sets = {"permissions_override": dict(before.get("permissions_override") or {})}
        if before.get("confirmed") is None:
            unset[PACKAGE_CONFIRMED_KEYS_FIELD] = ""
        else:
            sets[PACKAGE_CONFIRMED_KEYS_FIELD] = list(before.get("confirmed") or [])
        if before.get("normalized") is True:
            sets[MANUAL_GRANT_NORMALIZED_FIELD] = True
        else:
            unset[MANUAL_GRANT_NORMALIZED_FIELD] = ""
        query = {"role": doc_id} if kind == "role" else {row.get("identity_field") or "email": doc_id}
        query.update(_grant_filter(kind, live))
        collection = db.role_permissions if kind == "role" else db.users
        result = await collection.update_one(query, {"$set": sets, "$unset": unset})
        if result.matched_count != 1:
            raise CutoverConflict(
                [{"kind": kind, "id": doc_id, "reason": "changed_during_rollback"}],
                wrote=bool(restored),
            )
        restored.append({"kind": kind, "id": doc_id})
    return {
        "mode": "apply" if apply else "dry-run",
        "restored": len(restored),
        "documents": restored,
    }
