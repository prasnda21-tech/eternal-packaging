"""Idempotent production-readiness helpers for PWA Sales.

Default is dry-run. APPLY requires an explicit flag. Never invoked at startup.
Never targets production unless --allow-production is set by an operator.
users.id is discovered from email + employee_id + role. No hardcoded UUID.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional

from services.sales_user_mapping import (
    CANONICAL_SALES_PROFILES,
    SETTINGS_KEY,
    SALES_PWA_PERM,
    SALES_PWA_ROLE,
    discover_canonical_users,
    exact_sales_label,
    mapping_dict_from_entries,
    now_iso,
    public_mapping,
)

PRODUCTION_DB_NAMES = frozenset({"eternal_packaging"})
PREVIEW_OR_LOCAL_MARKERS = ("preview", "_test", "_local", "_dev")
LEAKY_SALES_ETERNAL_PERMS = frozenset({
    "po.view",
    "sales_order.view",
    "sales.delivery.view",
    "sales.invoice.view",
    "sales.receipt.view",
    "sales.customer.view",
    "design.view",
    "payment.view",
    "schedule.view",
    "schedule.work_date.view",
    "delivery_note.view",
    "delivery_note.view_file",
    "reports.order_prediction.view",
})


def require_explicit_db_name(db_name: Optional[str], *, apply: bool) -> str:
    name = str(db_name or "").strip()
    if not name:
        raise RuntimeError("--db-name wajib diisi")
    if apply and not name:
        raise RuntimeError("--db-name wajib saat --apply")
    return name


def refuse_db_target(db_name: str, *, allow_production: bool) -> str:
    name = str(db_name or "").strip()
    if not name:
        raise RuntimeError("DB name wajib diisi")
    is_production = name in PRODUCTION_DB_NAMES
    looks_preview_or_local = (not is_production) and (
        any(marker in name for marker in PREVIEW_OR_LOCAL_MARKERS)
        or name in {"test", "local", "dev"}
    )
    if is_production and not allow_production:
        raise RuntimeError(
            f"Refusing database {name!r}. Pass --allow-production only after explicit operator approval."
        )
    if allow_production and not is_production:
        raise RuntimeError(
            f"--allow-production ditolak: {name!r} bukan database production"
            + (" (preview/local)" if looks_preview_or_local else "")
        )
    return name


def refuse_production_db(db_name: str, *, allow_production: bool) -> None:
    refuse_db_target(db_name, allow_production=allow_production)


def _entries_to_map(entries: Optional[List[dict]]) -> Dict[str, str]:
    return mapping_dict_from_entries(entries)


def plan_sales_user_mapping(existing_doc: Optional[dict], users: Any) -> dict:
    """Plan Accurate label → discovered users.id. Exact labels only. Fail-closed."""
    existing = public_mapping(existing_doc)
    current = list(existing.get("entries") or [])
    current_map = _entries_to_map(current)
    planned = list(current)
    planned_map = dict(current_map)
    actions: List[str] = []
    discovered, errors = discover_canonical_users(users)
    canonical_ids = {row["user_id"] for row in discovered.values()}

    for key, row in discovered.items():
        label = exact_sales_label(row["accurate_label"])
        uid = row["user_id"]
        profile = row["profile"]
        user = row["user"]
        email = exact_sales_label(user.get("email"))
        emp = exact_sales_label(user.get("employee_id"))
        if email != exact_sales_label(profile.get("email")):
            errors.append(f"{key}: email mismatch {email!r}")
            continue
        if emp != exact_sales_label(profile.get("employee_id")):
            errors.append(f"{key}: employee_id mismatch {emp!r}")
            continue
        existing_uid = planned_map.get(label)
        if existing_uid and existing_uid != uid:
            errors.append(f"konflik label {label!r}: existing={existing_uid} canonical={uid}")
            continue
        if existing_uid == uid:
            actions.append(f"idempotent keep {label} → discovered users.id")
            continue
        planned.append({"label": label, "user_id": uid})
        planned_map[label] = uid
        actions.append(f"add {label} → discovered users.id")

    return {
        "ok": not errors,
        "errors": errors,
        "actions": actions,
        "before": current,
        "after": planned,
        "changed": current_map != planned_map,
        "discovered": {
            key: {
                "user_id": row["user_id"],
                "email": row["user"].get("email"),
                "employee_id": row["user"].get("employee_id"),
                "role": row["user"].get("role"),
                "accurate_label": row["accurate_label"],
            }
            for key, row in discovered.items()
        },
        "mutable_user_ids": sorted(canonical_ids),
        "profiles": [
            {
                "key": p["key"],
                "accurate_label": p["accurate_label"],
                "email": p["email"],
                "employee_id": p["employee_id"],
                "role": p["role"],
            }
            for p in CANONICAL_SALES_PROFILES
        ],
    }


async def backup_mapping_doc(db, dest_collection: str = "sales_user_mapping_backups") -> Optional[dict]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    if not doc:
        return None
    payload = {
        "backed_up_at": now_iso(),
        "key": SETTINGS_KEY,
        "snapshot": doc,
    }
    await db[dest_collection].insert_one(dict(payload))
    return payload


async def apply_sales_user_mapping(db, plan: dict, actor: Optional[dict] = None) -> dict:
    if not plan.get("ok"):
        raise ValueError("plan has errors; refuse apply")
    actor = actor or {}
    if not plan.get("changed"):
        return {
            "applied": True,
            "idempotent": True,
            "backup": None,
            "verified": public_mapping(await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})),
            "rollback": {
                "collection": "sales_user_mapping_backups",
                "restore": "copy snapshot.entries back to app_settings.key=sales_user_mapping",
            },
        }
    previous = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    backup = await backup_mapping_doc(db)
    doc = {
        "key": SETTINGS_KEY,
        "entries": plan["after"],
        "updated_at": now_iso(),
        "updated_by": actor.get("email") or "mapping-script",
        "updated_by_name": actor.get("name") or "mapping-script",
        "updated_by_role": actor.get("role") or "script",
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": doc}, upsert=True)
    await db.audit_logs.insert_one({
        "action": "sales_user_mapping.update",
        "actor_email": actor.get("email") or "mapping-script",
        "actor_role": actor.get("role") or "script",
        "actor_name": actor.get("name") or "mapping-script",
        "target_email": SETTINGS_KEY,
        "before": {"entries": (previous or {}).get("entries") or []},
        "after": {"entries": plan["after"]},
        "note": "Idempotent canonical Accurate sales mapping",
        "at": now_iso(),
        "backup_at": (backup or {}).get("backed_up_at"),
    })
    verify = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return {
        "applied": True,
        "idempotent": False,
        "backup": {k: backup.get(k) for k in ("backed_up_at", "key") if backup},
        "verified": public_mapping(verify),
        "rollback": {
            "collection": "sales_user_mapping_backups",
            "restore": "copy snapshot.entries back to app_settings.key=sales_user_mapping",
        },
    }


def plan_sales_eternal_permissions(
    *,
    role_snapshot: Optional[List[str]],
    existing_users: List[dict],
    mutable_user_ids: Optional[List[str]] = None,
) -> dict:
    """Permission dry-run. User-document writes only for discovered Ayub and Euis."""
    actions: List[str] = []
    errors: List[str] = []
    snapshot_needed = False
    new_snapshot = None
    if role_snapshot is None:
        actions.append("no role_permissions snapshot; ROLE_DEFAULT_PERMISSIONS applies after deploy")
    else:
        snapshot_needed = True
        new_snapshot = sorted(set(role_snapshot) | {SALES_PWA_PERM})
        removed = sorted(set(new_snapshot) & LEAKY_SALES_ETERNAL_PERMS)
        new_snapshot = sorted(set(new_snapshot) - LEAKY_SALES_ETERNAL_PERMS)
        if SALES_PWA_PERM not in role_snapshot:
            actions.append("add sales.pwa.view to role_permissions.sales_eternal")
        else:
            actions.append("role snapshot already has sales.pwa.view")
        if removed:
            actions.append("remove leaky global view keys from snapshot: " + ", ".join(removed))
        if sorted(role_snapshot) == new_snapshot:
            actions.append("snapshot already matches hardened sales_eternal")
            snapshot_needed = False

    allowed_ids = {str(uid).strip() for uid in (mutable_user_ids or []) if str(uid).strip()}
    if mutable_user_ids is None:
        discovered, disc_errors = discover_canonical_users(existing_users)
        errors.extend(disc_errors)
        allowed_ids = {row["user_id"] for row in discovered.values()}
    elif len(allowed_ids) != 2:
        errors.append("mutable_user_ids wajib tepat Ayub dan Euis (2 users.id)")

    user_reports = []
    skipped_other_users = []
    for u in existing_users:
        uid = str(u.get("id") or "").strip()
        override = dict(u.get("permissions_override") or {})
        leaky_true = sorted(k for k in override if override.get(k) is True and k in LEAKY_SALES_ETERNAL_PERMS)
        pwa_false = override.get(SALES_PWA_PERM) is False
        mutable = uid in allowed_ids
        report = {
            "id": uid,
            "email": u.get("email"),
            "employee_id": u.get("employee_id"),
            "role": u.get("role"),
            "leaky_override_true": leaky_true if mutable else [],
            "pwa_view_denied_by_override": pwa_false if mutable else False,
            "mutable": mutable,
        }
        user_reports.append(report)
        if not mutable:
            skipped_other_users.append(u.get("email") or uid)
            continue
        if pwa_false:
            errors.append(f"{u.get('email')}: permissions_override denies {SALES_PWA_PERM}")
        if leaky_true:
            actions.append(f"{u.get('email')}: would clear override True for {', '.join(leaky_true)}")

    return {
        "ok": not errors,
        "errors": errors,
        "actions": actions,
        "snapshot_before": role_snapshot,
        "snapshot_after": new_snapshot if snapshot_needed else role_snapshot,
        "snapshot_changed": snapshot_needed and new_snapshot != sorted(role_snapshot or []),
        "users": user_reports,
        "mutable_user_ids": sorted(allowed_ids),
        "skipped_other_users": skipped_other_users,
    }


async def backup_permissions_checkpoint(db, plan: dict) -> dict:
    snap = await db.role_permissions.find_one({"role": SALES_PWA_ROLE}, {"_id": 0})
    ids = list(plan.get("mutable_user_ids") or [])
    users = []
    if ids:
        users = await db.users.find(
            {"id": {"$in": ids}},
            {"_id": 0, "id": 1, "email": 1, "employee_id": 1, "role": 1, "permissions_override": 1},
        ).to_list(len(ids))
    payload = {
        "backed_up_at": now_iso(),
        "role": SALES_PWA_ROLE,
        "role_snapshot": snap,
        "users": users,
    }
    await db.sales_eternal_permissions_backups.insert_one(dict(payload))
    return payload


async def apply_sales_eternal_permissions(db, plan: dict, actor: Optional[dict] = None) -> dict:
    if not plan.get("ok"):
        raise ValueError("plan has errors; refuse apply")
    actor = actor or {}
    mutable_ids = {str(uid).strip() for uid in (plan.get("mutable_user_ids") or []) if str(uid).strip()}
    if len(mutable_ids) != 2:
        raise ValueError("refuse apply: mutable_user_ids must be exactly Ayub and Euis")
    user_changes = [
        row for row in (plan.get("users") or [])
        if row.get("mutable") and (row.get("leaky_override_true") or [])
    ]
    needs_write = bool(plan.get("snapshot_changed") or user_changes)
    if not needs_write:
        return {"applied": True, "idempotent": True, "backup": None}
    backup = await backup_permissions_checkpoint(db, plan)
    if plan.get("snapshot_changed"):
        before = await db.role_permissions.find_one({"role": SALES_PWA_ROLE}, {"_id": 0})
        await db.role_permissions.update_one(
            {"role": SALES_PWA_ROLE},
            {"$set": {
                "role": SALES_PWA_ROLE,
                "permissions": plan["snapshot_after"],
                "updated_at": now_iso(),
                "updated_by": actor.get("email") or "permissions-script",
            }},
            upsert=True,
        )
        await db.audit_logs.insert_one({
            "action": "role_permissions.update",
            "actor_email": actor.get("email") or "permissions-script",
            "actor_role": actor.get("role") or "script",
            "target_email": SALES_PWA_ROLE,
            "before": before,
            "after": {"permissions": plan["snapshot_after"]},
            "note": "Add sales.pwa.view; strip leaky global view keys",
            "at": now_iso(),
        })
    for row in user_changes:
        uid = str(row.get("id") or "").strip()
        if uid not in mutable_ids:
            continue
        user = await db.users.find_one({"id": uid}, {"_id": 0, "permissions_override": 1, "role": 1})
        if exact_sales_label((user or {}).get("role")) != SALES_PWA_ROLE:
            raise ValueError(f"refuse apply: {uid} role is not {SALES_PWA_ROLE}")
        override = dict((user or {}).get("permissions_override") or {})
        before = copy.deepcopy(override)
        for key in row.get("leaky_override_true") or []:
            override[key] = False
        await db.users.update_one({"id": uid}, {"$set": {"permissions_override": override, "updated_at": now_iso()}})
        await db.audit_logs.insert_one({
            "action": "user.permissions_override.update",
            "actor_email": actor.get("email") or "permissions-script",
            "target_email": row.get("email"),
            "before": before,
            "after": override,
            "note": "Clear leaky global view overrides for canonical sales_eternal only",
            "at": now_iso(),
        })
    return {
        "applied": True,
        "idempotent": False,
        "backup": {"backed_up_at": backup.get("backed_up_at"), "collection": "sales_eternal_permissions_backups"},
    }


def canonical_user_query() -> dict:
    emails = [exact_sales_label(p["email"]) for p in CANONICAL_SALES_PROFILES]
    emps = [exact_sales_label(p["employee_id"]) for p in CANONICAL_SALES_PROFILES]
    return {
        "$or": [
            {"email": {"$in": emails}},
            {"employee_id": {"$in": emps}},
        ]
    }
