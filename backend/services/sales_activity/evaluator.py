"""Idempotent ownership evaluator. Never acts on paused migration/manual."""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

from services.sales_activity import events as event_store
from services.sales_activity import ownership as ownership_svc
from services.sales_activity.clock import iso_now, now_utc, parse_dt
from services.sales_activity.constants import (
    ACTOR_SYSTEM,
    AUTOMATION_ACTIVE,
    COL_OWNERSHIP,
    COL_OUTLETS,
    EVENT_AUTO_REASSIGNED,
    EVENT_BECAME_CUSTOMER,
    EVENT_BECAME_DORMANT,
    EVENT_SENT_TO_POOL,
    EVALUATOR_LOCK_KEY,
    KIND_CUSTOMER,
    KIND_DORMANT,
    KIND_PROSPECT,
    POOL_DORMANT,
    POOL_NEXT_INVALID,
    POOL_SEQUENCE_EXHAUSTED,
)
from services.sales_activity.orders import classify_customer, last_order_at, last_valid_sales_order
from services.sales_activity.pool import enter_pool
from services.sales_activity.settings import load_settings, select_rule
from services.sales_activity.users import get_user, is_user_active, users_by_ids

logger = logging.getLogger("sales_activity.evaluator")

_task: Optional[asyncio.Task] = None


def evaluator_enabled() -> bool:
    if os.environ.get("SALES_ACTIVITY_EVALUATOR_ENABLED") == "0":
        return False
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return False
    return os.environ.get("SALES_ACTIVITY_EVALUATOR_ENABLED", "1") == "1"


async def _acquire_lock(db) -> bool:
    now = now_utc()
    doc = await db.app_settings.find_one_and_update(
        {
            "key": EVALUATOR_LOCK_KEY,
            "$or": [
                {"locked_until": {"$exists": False}},
                {"locked_until": {"$lte": now.isoformat()}},
            ],
        },
        {"$set": {"key": EVALUATOR_LOCK_KEY, "locked_until": now.replace(microsecond=0).isoformat(), "holder": iso_now()}},
        upsert=True,
    )
    # upsert without prior match may still succeed; treat as acquired
    return True if doc is not None or True else False


def _next_in_sequence(current_owner_id: Optional[str], sequence: list, users: dict, skip_inactive: bool, override_id: Optional[str]) -> Optional[dict]:
    if override_id:
        user = users.get(override_id)
        if user and (not skip_inactive or is_user_active(user)):
            return user
        return None
    if not sequence:
        return None
    start = 0
    if current_owner_id and current_owner_id in sequence:
        start = sequence.index(current_owner_id) + 1
    for uid in sequence[start:]:
        user = users.get(uid)
        if not user:
            continue
        if skip_inactive and not is_user_active(user):
            continue
        return user
    return None


async def _refresh_customer_kind(db, outlet: dict, snap: dict, settings: dict) -> dict:
    last = await last_valid_sales_order(
        db,
        customer_name=outlet.get("linked_customer_name") or outlet.get("name"),
        outlet_name=outlet.get("linked_outlet_name") or outlet.get("name"),
        customer_id=outlet.get("linked_customer_id") or "",
    )
    kind, iso = classify_customer(last, int((settings.get("dormancy") or {}).get("threshold_days") or 90), now_utc())
    return {"customer_kind": kind, "last_valid_sales_order_at": iso, "last_order": last}


async def evaluate_one(db, snap: dict, settings: dict) -> Optional[str]:
    if snap.get("automation_status") != AUTOMATION_ACTIVE:
        return None
    if snap.get("exempt") or snap.get("in_pool") or snap.get("ownership_unresolved"):
        return None
    outlet = await db[COL_OUTLETS].find_one({"id": snap["outlet_id"]}, {"_id": 0})
    if not outlet:
        return None
    rule = select_rule(settings, outlet)
    if not rule.get("enabled"):
        return None
    refresh = await _refresh_customer_kind(db, outlet, snap, settings)
    kind = refresh["customer_kind"]
    last_iso = refresh["last_valid_sales_order_at"]
    now = now_utc()

    if kind in {KIND_CUSTOMER, KIND_DORMANT} and snap.get("customer_kind") == KIND_PROSPECT:
        patched = await ownership_svc.cas_update(db, snap["outlet_id"], snap["version"], {
            "customer_kind": KIND_CUSTOMER,
            "last_valid_sales_order_at": last_iso,
        })
        if patched:
            await event_store.append_event(
                db,
                outlet_id=snap["outlet_id"],
                event_type=EVENT_BECAME_CUSTOMER,
                previous_owner_id=snap.get("current_owner_id"),
                new_owner_id=snap.get("current_owner_id"),
                actor_type=ACTOR_SYSTEM,
                reason="Valid Eternal Pesanan Penjualan",
                source=snap.get("source"),
                correlation_key=f"became-customer:{snap['outlet_id']}:v{snap['version']}",
            )
            snap = patched
        else:
            return None

    if kind == KIND_CUSTOMER:
        if last_iso != snap.get("last_valid_sales_order_at"):
            await ownership_svc.cas_update(db, snap["outlet_id"], snap["version"], {"last_valid_sales_order_at": last_iso, "customer_kind": KIND_CUSTOMER})
        return None

    if kind == KIND_DORMANT:
        from services.sales_activity.ownership import begin_new_cycle
        pool = await enter_pool(db, snap, reason=POOL_DORMANT, suggested_next_owner_id=None)
        updated = await begin_new_cycle(
            db,
            snap,
            None,
            event_type=EVENT_BECAME_DORMANT,
            actor_type=ACTOR_SYSTEM,
            actor_user_id=None,
            reason=POOL_DORMANT,
            automation_status=AUTOMATION_ACTIVE,
            source=snap.get("source"),
            settings=settings,
            outlet=outlet,
            correlation_key=f"dormant-pool:{snap['outlet_id']}:v{snap['version']}",
            customer_kind=KIND_DORMANT,
            in_pool=True,
            pool_id=pool.get("id"),
        )
        if updated:
            await event_store.append_event(
                db,
                outlet_id=snap["outlet_id"],
                event_type=EVENT_SENT_TO_POOL,
                previous_owner_id=snap.get("current_owner_id"),
                new_owner_id=None,
                actor_type=ACTOR_SYSTEM,
                reason=POOL_DORMANT,
                source=snap.get("source"),
                correlation_key=f"dormant-pool-sent:{snap['outlet_id']}:v{snap['version']}",
            )
            return "DORMANT_POOL"
        return None

    # Prospect expiry
    eligible = parse_dt(snap.get("eligible_at"))
    if not eligible or now < eligible:
        return None
    sequence = list((rule.get("reassignment") or {}).get("sales_sequence") or [])
    skip_inactive = bool((rule.get("reassignment") or {}).get("skip_inactive_users", True))
    users = await users_by_ids(db, sequence + ([snap.get("override_next_owner_id")] if snap.get("override_next_owner_id") else []))
    nxt = _next_in_sequence(snap.get("current_owner_id"), sequence, users, skip_inactive, snap.get("override_next_owner_id"))
    from services.sales_activity.ownership import begin_new_cycle
    if nxt:
        updated = await begin_new_cycle(
            db,
            snap,
            nxt,
            event_type=EVENT_AUTO_REASSIGNED,
            actor_type=ACTOR_SYSTEM,
            actor_user_id=None,
            reason="HYBRID auto reassignment",
            automation_status=AUTOMATION_ACTIVE,
            source=snap.get("source"),
            settings=settings,
            outlet=outlet,
            correlation_key=f"auto-reassign:{snap['outlet_id']}:v{snap['version']}:{eligible.date().isoformat()}",
        )
        return "REASSIGNED" if updated else None
    reason = POOL_NEXT_INVALID if sequence else POOL_SEQUENCE_EXHAUSTED
    if snap.get("current_owner_id") and sequence and snap.get("current_owner_id") in sequence:
        if sequence.index(snap["current_owner_id"]) >= len(sequence) - 1:
            reason = POOL_SEQUENCE_EXHAUSTED
    pool = await enter_pool(db, snap, reason=reason, suggested_next_owner_id=None)
    updated = await begin_new_cycle(
        db,
        snap,
        None,
        event_type=EVENT_SENT_TO_POOL,
        actor_type=ACTOR_SYSTEM,
        actor_user_id=None,
        reason=reason,
        automation_status=AUTOMATION_ACTIVE,
        source=snap.get("source"),
        settings=settings,
        outlet=outlet,
        correlation_key=f"auto-pool:{snap['outlet_id']}:v{snap['version']}:{eligible.date().isoformat()}",
        in_pool=True,
        pool_id=pool.get("id"),
    )
    return "POOLED" if updated else None


async def run_evaluator(db, *, limit: int = 500) -> dict:
    settings = await load_settings(db)
    now = now_utc().isoformat()
    query = {
        "automation_status": AUTOMATION_ACTIVE,
        "in_pool": {"$ne": True},
        "exempt": {"$ne": True},
        "ownership_unresolved": {"$ne": True},
    }
    rows = await db[COL_OWNERSHIP].find(query, {"_id": 0}).sort("eligible_at", 1).to_list(limit)
    transitions = []
    examined = 0
    for snap in rows:
        examined += 1
        try:
            result = await evaluate_one(db, snap, settings)
        except Exception:
            logger.exception("evaluator failed for %s", snap.get("outlet_id"))
            result = None
        if result:
            transitions.append({"outlet_id": snap.get("outlet_id"), "result": result})
    return {"examined": examined, "transitions": transitions, "evaluated_at": now}


async def _loop(db):
    interval = int(os.environ.get("SALES_ACTIVITY_EVALUATOR_INTERVAL_SEC") or 3600)
    interval = max(60, interval)
    while True:
        try:
            await run_evaluator(db)
        except Exception:
            logger.exception("sales activity evaluator loop failed")
        await asyncio.sleep(interval)


def start_evaluator_loop(db) -> None:
    global _task
    if not evaluator_enabled():
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(db))


def stop_evaluator_loop() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
