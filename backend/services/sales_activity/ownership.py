"""Current ownership snapshot + manual interventions."""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.sales_activity import events as event_store
from services.sales_activity.clock import iso_now, now_utc, parse_dt
from services.sales_activity.constants import (
    ACTOR_SYSTEM,
    ACTOR_USER,
    AUTOMATION_ACTIVE,
    AUTOMATION_PAUSED_MANUAL,
    AUTOMATION_PAUSED_MIGRATION,
    COL_OWNERSHIP,
    COL_OUTLETS,
    COL_POOL,
    EVENT_ASSIGNED_FROM_POOL,
    EVENT_CLAIMED,
    EVENT_CUSTOM_EXPIRY,
    EVENT_EXEMPTED,
    EVENT_EXTENDED,
    EVENT_MANUAL_REASSIGNED,
    EVENT_MIGRATION_ACTIVATED,
    EVENT_OVERRIDE_NEXT,
    EVENT_PAUSED,
    EVENT_RESUMED,
    EVENT_SENT_TO_POOL,
    EVENT_UNEXEMPTED,
    KIND_CUSTOMER,
    KIND_DORMANT,
    KIND_PROSPECT,
    PERM_OWNERSHIP_MANAGE,
    PERM_POOL_MANAGE,
    POOL_MANUAL,
    SOURCE_IMPORT,
    SOURCE_NATIVE,
)
from services.sales_activity.settings import load_settings, rule_snapshot, select_rule
from services.sales_activity.users import get_user, is_user_active
from services.sales_activity.util import compute_windows, new_id, public_doc, require_reason, stamp_created


def empty_ownership(outlet_id: str, *, source: str, automation_status: str) -> dict:
    return stamp_created({
        "id": new_id(),
        "outlet_id": outlet_id,
        "current_owner_id": None,
        "current_owner_name": None,
        "ownership_started_at": None,
        "timer_anchor_at": None,
        "eligible_at": None,
        "warning_at": None,
        "inactivity_eligible_at": None,
        "automation_status": automation_status,
        "source": source,
        "rule_id": None,
        "rule_version": None,
        "rule_snapshot": None,
        "in_pool": False,
        "pool_id": None,
        "pause_reason": None,
        "paused_at": None,
        "pause_remaining_seconds": None,
        "override_next_owner_id": None,
        "exempt": False,
        "version": 1,
        "customer_kind": KIND_PROSPECT,
        "last_valid_sales_order_at": None,
        "last_activity_at": None,
        "next_follow_up_at": None,
        "import_batch_id": None,
        "ownership_unresolved": False,
        "unresolved_reason": None,
        "reassignment_count": 0,
    })


async def get_ownership(db, outlet_id: str) -> Optional[dict]:
    return await db[COL_OWNERSHIP].find_one({"outlet_id": outlet_id}, {"_id": 0})


async def replace_ownership(db, snap: dict) -> dict:
    snap["updated_at"] = iso_now()
    await db[COL_OWNERSHIP].replace_one({"outlet_id": snap["outlet_id"]}, snap, upsert=True)
    return public_doc(snap)


async def cas_update(db, outlet_id: str, version: int, patch: dict) -> Optional[dict]:
    patch = dict(patch)
    patch["updated_at"] = iso_now()
    patch["version"] = int(version) + 1
    result = await db[COL_OWNERSHIP].find_one_and_update(
        {"outlet_id": outlet_id, "version": int(version)},
        {"$set": patch},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0},
    )
    return result


def apply_rule_windows(snap: dict, rule: dict, *, started_at=None, timer_anchor_at=None) -> dict:
    started = started_at or snap.get("ownership_started_at") or iso_now()
    anchor = timer_anchor_at or snap.get("timer_anchor_at") or started
    windows = compute_windows(started, anchor, rule)
    snap.update(windows)
    snap["rule_id"] = rule.get("rule_id")
    snap["rule_version"] = rule.get("rule_version")
    return snap


async def start_native_cycle(db, outlet: dict, owner: dict, settings: dict, actor: dict) -> dict:
    rule = select_rule(settings, outlet)
    snap = empty_ownership(outlet["id"], source=SOURCE_NATIVE, automation_status=AUTOMATION_ACTIVE)
    now = iso_now()
    snap["current_owner_id"] = owner.get("id")
    snap["current_owner_name"] = owner.get("name")
    snap["rule_snapshot"] = rule_snapshot(settings)
    apply_rule_windows(snap, rule, started_at=now, timer_anchor_at=now)
    await replace_ownership(db, snap)
    await event_store.append_event(
        db,
        outlet_id=outlet["id"],
        event_type=EVENT_CLAIMED,
        previous_owner_id=None,
        new_owner_id=owner.get("id"),
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id") or owner.get("id"),
        rule_id=rule.get("rule_id"),
        rule_version=rule.get("rule_version"),
        reason="Native outlet create",
        source=SOURCE_NATIVE,
        correlation_key=f"claim:{outlet['id']}:v1",
    )
    return snap


def can_manage_ownership(perms: set) -> bool:
    return PERM_OWNERSHIP_MANAGE in perms


def can_manage_pool(perms: set) -> bool:
    return PERM_POOL_MANAGE in perms or PERM_OWNERSHIP_MANAGE in perms


async def _require_manage(perms: set, *, pool: bool = False):
    if pool:
        if not can_manage_pool(perms):
            raise HTTPException(403, f"Permission '{PERM_POOL_MANAGE}' diperlukan")
        return
    if not can_manage_ownership(perms):
        raise HTTPException(403, f"Permission '{PERM_OWNERSHIP_MANAGE}' diperlukan")


async def pause_automation(db, outlet_id: str, reason: str, actor: dict, perms: set) -> dict:
    await _require_manage(perms)
    reason = require_reason(reason)
    snap = await get_ownership(db, outlet_id)
    if not snap:
        raise HTTPException(404, "Ownership tidak ditemukan")
    if snap.get("automation_status") == AUTOMATION_PAUSED_MIGRATION:
        raise HTTPException(409, "Data migrasi masih PAUSED_MIGRATION. Aktifkan migrasi terlebih dahulu.")
    if snap.get("automation_status") == AUTOMATION_PAUSED_MANUAL:
        return snap
    eligible = parse_dt(snap.get("eligible_at"))
    remaining = None
    if eligible:
        remaining = max(0, int((eligible - now_utc()).total_seconds()))
    updated = await cas_update(db, outlet_id, snap["version"], {
        "automation_status": AUTOMATION_PAUSED_MANUAL,
        "pause_reason": reason,
        "paused_at": iso_now(),
        "pause_remaining_seconds": remaining,
    })
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    await event_store.append_event(
        db,
        outlet_id=outlet_id,
        event_type=EVENT_PAUSED,
        previous_owner_id=snap.get("current_owner_id"),
        new_owner_id=snap.get("current_owner_id"),
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        rule_id=snap.get("rule_id"),
        rule_version=snap.get("rule_version"),
        reason=reason,
        source=snap.get("source"),
        correlation_key=f"pause:{outlet_id}:v{snap['version']}",
    )
    return updated


async def resume_automation(db, outlet_id: str, reason: str, actor: dict, perms: set) -> dict:
    await _require_manage(perms)
    reason = require_reason(reason)
    snap = await get_ownership(db, outlet_id)
    if not snap:
        raise HTTPException(404, "Ownership tidak ditemukan")
    if snap.get("automation_status") == AUTOMATION_PAUSED_MIGRATION:
        raise HTTPException(409, "Gunakan aktivasi migrasi, bukan resume.")
    if snap.get("automation_status") != AUTOMATION_PAUSED_MANUAL:
        return snap
    remaining = snap.get("pause_remaining_seconds")
    patch = {
        "automation_status": AUTOMATION_ACTIVE,
        "pause_reason": None,
        "paused_at": None,
        "pause_remaining_seconds": None,
    }
    if remaining is not None:
        new_eligible = now_utc() + timedelta(seconds=int(remaining))
        warning_days = 0
        settings = await load_settings(db)
        outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
        rule = select_rule(settings, outlet)
        warning_days = int(rule.get("warning_before_expiration_days") or 0)
        patch["eligible_at"] = new_eligible.isoformat()
        patch["warning_at"] = (new_eligible - timedelta(days=warning_days)).isoformat()
    updated = await cas_update(db, outlet_id, snap["version"], patch)
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    await event_store.append_event(
        db,
        outlet_id=outlet_id,
        event_type=EVENT_RESUMED,
        previous_owner_id=snap.get("current_owner_id"),
        new_owner_id=snap.get("current_owner_id"),
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        rule_id=snap.get("rule_id"),
        rule_version=snap.get("rule_version"),
        reason=reason,
        source=snap.get("source"),
        correlation_key=f"resume:{outlet_id}:v{snap['version']}",
    )
    return updated


async def extend_or_custom_expiry(db, outlet_id: str, *, reason: str, actor: dict, perms: set, extra_days: Optional[int] = None, custom_expiry: Optional[str] = None) -> dict:
    await _require_manage(perms)
    reason = require_reason(reason)
    snap = await get_ownership(db, outlet_id)
    if not snap:
        raise HTTPException(404, "Ownership tidak ditemukan")
    if extra_days is None and not custom_expiry:
        raise HTTPException(400, "extra_days atau custom_expiry wajib")
    if extra_days is not None:
        if int(extra_days) < 1:
            raise HTTPException(400, "extra_days harus positif")
        base = parse_dt(snap.get("eligible_at")) or now_utc()
        new_eligible = base + timedelta(days=int(extra_days))
        event_type = EVENT_EXTENDED
    else:
        new_eligible = parse_dt(custom_expiry)
        if not new_eligible:
            raise HTTPException(400, "custom_expiry tidak valid")
        event_type = EVENT_CUSTOM_EXPIRY
    settings = await load_settings(db)
    outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
    rule = select_rule(settings, outlet)
    warning = new_eligible - timedelta(days=int(rule.get("warning_before_expiration_days") or 0))
    updated = await cas_update(db, outlet_id, snap["version"], {
        "eligible_at": new_eligible.isoformat(),
        "warning_at": warning.isoformat(),
    })
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    await event_store.append_event(
        db,
        outlet_id=outlet_id,
        event_type=event_type,
        previous_owner_id=snap.get("current_owner_id"),
        new_owner_id=snap.get("current_owner_id"),
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        rule_id=snap.get("rule_id"),
        rule_version=snap.get("rule_version"),
        reason=reason,
        source=snap.get("source"),
        correlation_key=f"{event_type}:{outlet_id}:v{snap['version']}",
        payload={"eligible_at": new_eligible.isoformat()},
    )
    return updated


async def override_next_sales(db, outlet_id: str, next_owner_id: str, reason: str, actor: dict, perms: set) -> dict:
    await _require_manage(perms)
    reason = require_reason(reason)
    target = await get_user(db, next_owner_id)
    if not target or not is_user_active(target):
        raise HTTPException(400, "Sales tujuan tidak valid")
    snap = await get_ownership(db, outlet_id)
    if not snap:
        raise HTTPException(404, "Ownership tidak ditemukan")
    updated = await cas_update(db, outlet_id, snap["version"], {"override_next_owner_id": target["id"]})
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    await event_store.append_event(
        db,
        outlet_id=outlet_id,
        event_type=EVENT_OVERRIDE_NEXT,
        previous_owner_id=snap.get("current_owner_id"),
        new_owner_id=target["id"],
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        reason=reason,
        source=snap.get("source"),
        correlation_key=f"override-next:{outlet_id}:v{snap['version']}",
    )
    return updated


async def set_exempt(db, outlet_id: str, exempt: bool, reason: str, actor: dict, perms: set) -> dict:
    await _require_manage(perms)
    reason = require_reason(reason)
    snap = await get_ownership(db, outlet_id)
    if not snap:
        raise HTTPException(404, "Ownership tidak ditemukan")
    updated = await cas_update(db, outlet_id, snap["version"], {"exempt": bool(exempt)})
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    await event_store.append_event(
        db,
        outlet_id=outlet_id,
        event_type=EVENT_EXEMPTED if exempt else EVENT_UNEXEMPTED,
        previous_owner_id=snap.get("current_owner_id"),
        new_owner_id=snap.get("current_owner_id"),
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        reason=reason,
        source=snap.get("source"),
        correlation_key=f"exempt:{outlet_id}:{int(exempt)}:v{snap['version']}",
    )
    return updated


async def begin_new_cycle(
    db,
    snap: dict,
    new_owner: Optional[dict],
    *,
    event_type: str,
    actor_type: str,
    actor_user_id: Optional[str],
    reason: str,
    automation_status: str,
    source: str,
    settings: dict,
    outlet: dict,
    import_batch_id: Optional[str] = None,
    correlation_key: Optional[str],
    customer_kind: Optional[str] = None,
    in_pool: bool = False,
    pool_id: Optional[str] = None,
) -> Optional[dict]:
    rule = select_rule(settings, outlet)
    now = iso_now()
    previous = snap.get("current_owner_id")
    patch = {
        "current_owner_id": (new_owner or {}).get("id"),
        "current_owner_name": (new_owner or {}).get("name"),
        "automation_status": automation_status,
        "source": source,
        "in_pool": in_pool,
        "pool_id": pool_id,
        "pause_reason": None,
        "paused_at": None,
        "pause_remaining_seconds": None,
        "override_next_owner_id": None,
        "ownership_unresolved": False,
        "unresolved_reason": None,
        "reassignment_count": int(snap.get("reassignment_count") or 0) + (1 if previous and new_owner and previous != new_owner.get("id") else 0),
        "rule_snapshot": rule_snapshot(settings),
        "customer_kind": customer_kind or snap.get("customer_kind") or KIND_PROSPECT,
    }
    if new_owner and not in_pool and automation_status == AUTOMATION_ACTIVE:
        windows = compute_windows(now, now, rule)
        patch.update(windows)
        patch["rule_id"] = rule.get("rule_id")
        patch["rule_version"] = rule.get("rule_version")
    elif in_pool or not new_owner:
        patch.update({
            "ownership_started_at": snap.get("ownership_started_at"),
            "timer_anchor_at": None,
            "eligible_at": None,
            "warning_at": None,
            "inactivity_eligible_at": None,
            "rule_id": rule.get("rule_id"),
            "rule_version": rule.get("rule_version"),
        })
    updated = await cas_update(db, snap["outlet_id"], snap["version"], patch)
    if not updated:
        return None
    await event_store.append_event(
        db,
        outlet_id=snap["outlet_id"],
        event_type=event_type,
        previous_owner_id=previous,
        new_owner_id=(new_owner or {}).get("id"),
        actor_type=actor_type,
        actor_user_id=actor_user_id,
        rule_id=rule.get("rule_id"),
        rule_version=rule.get("rule_version"),
        reason=reason,
        source=source,
        import_batch_id=import_batch_id,
        correlation_key=correlation_key,
    )
    return updated


async def manual_reassign(db, outlet_id: str, new_owner_id: str, reason: str, actor: dict, perms: set) -> dict:
    await _require_manage(perms)
    reason = require_reason(reason)
    snap = await get_ownership(db, outlet_id)
    if not snap:
        raise HTTPException(404, "Ownership tidak ditemukan")
    if snap.get("in_pool"):
        raise HTTPException(409, "Outlet ada di Pool. Gunakan assign dari Pool.")
    settings = await load_settings(db)
    outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
    # Outlet Prospek: new owner must be on PIC Sales Prospek allowlist.
    # Other kinds keep existing supervisor reassignment rules.
    kind = str(snap.get("customer_kind") or (outlet or {}).get("customer_kind") or "").upper()
    if kind == KIND_PROSPECT:
        from services.sales_activity.outlets import resolve_prospect_pic_owner
        target = await resolve_prospect_pic_owner(db, settings, new_owner_id, actor)
    else:
        target = await get_user(db, new_owner_id)
        if not target or not is_user_active(target):
            raise HTTPException(400, "Sales tujuan tidak valid")
    updated = await begin_new_cycle(
        db,
        snap,
        target,
        event_type=EVENT_MANUAL_REASSIGNED,
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        reason=reason,
        automation_status=AUTOMATION_ACTIVE,
        source=snap.get("source") or SOURCE_NATIVE,
        settings=settings,
        outlet=outlet or {},
        correlation_key=f"manual-reassign:{outlet_id}:v{snap['version']}",
    )
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    return updated


async def send_to_pool_manual(db, outlet_id: str, reason: str, actor: dict, perms: set) -> dict:
    await _require_manage(perms)
    reason = require_reason(reason)
    from services.sales_activity.pool import enter_pool
    snap = await get_ownership(db, outlet_id)
    if not snap:
        raise HTTPException(404, "Ownership tidak ditemukan")
    if snap.get("in_pool"):
        return snap
    pool = await enter_pool(db, snap, reason=reason or POOL_MANUAL, suggested_next_owner_id=None)
    settings = await load_settings(db)
    outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
    updated = await begin_new_cycle(
        db,
        snap,
        None,
        event_type=EVENT_SENT_TO_POOL,
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        reason=reason,
        automation_status=AUTOMATION_ACTIVE,
        source=snap.get("source") or SOURCE_NATIVE,
        settings=settings,
        outlet=outlet or {},
        correlation_key=f"manual-pool:{outlet_id}:v{snap['version']}",
        in_pool=True,
        pool_id=pool.get("id"),
        customer_kind=snap.get("customer_kind"),
    )
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    return updated


async def assign_from_pool(db, outlet_id: str, new_owner_id: str, reason: str, actor: dict, perms: set) -> dict:
    await _require_manage(perms, pool=True)
    reason = require_reason(reason)
    target = await get_user(db, new_owner_id)
    if not target or not is_user_active(target):
        raise HTTPException(400, "Sales tujuan tidak valid")
    snap = await get_ownership(db, outlet_id)
    if not snap or not snap.get("in_pool"):
        raise HTTPException(409, "Outlet tidak berada di Pool")
    from services.sales_activity.pool import leave_pool
    await leave_pool(db, outlet_id)
    settings = await load_settings(db)
    outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
    updated = await begin_new_cycle(
        db,
        snap,
        target,
        event_type=EVENT_ASSIGNED_FROM_POOL,
        actor_type=ACTOR_USER,
        actor_user_id=actor.get("id"),
        reason=reason,
        automation_status=AUTOMATION_ACTIVE,
        source=SOURCE_NATIVE,
        settings=settings,
        outlet=outlet or {},
        correlation_key=f"pool-assign:{outlet_id}:v{snap['version']}",
        in_pool=False,
        pool_id=None,
        customer_kind=KIND_PROSPECT if snap.get("customer_kind") == KIND_DORMANT else snap.get("customer_kind"),
    )
    if not updated:
        raise HTTPException(409, "Ownership berubah. Ulangi.")
    return updated
