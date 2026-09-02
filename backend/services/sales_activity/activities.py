"""Sales activity records. Native creator is always the authenticated user."""
from __future__ import annotations

from fastapi import HTTPException

from services.sales_activity import events as event_store
from services.sales_activity import ownership as ownership_svc
from services.sales_activity import photos as photo_svc
from services.sales_activity.clock import iso_now, parse_dt
from services.sales_activity.constants import (
    AUTOMATION_ACTIVE,
    COL_ACTIVITIES,
    COL_OUTLETS,
    EVENT_ACTIVITY_TIMER,
    PERM_ACTIVITY_CREATE,
    PERM_OWNERSHIP_MANAGE,
    SOURCE_NATIVE,
)
from services.sales_activity.settings import activity_type_map, load_settings, select_rule
from services.sales_activity.util import actor_id, actor_name, compute_windows, new_id, public_doc, stamp_created


def _can_work_outlet(snap: dict, user: dict, perms: set) -> bool:
    if PERM_OWNERSHIP_MANAGE in perms:
        return True
    return bool(snap and snap.get("current_owner_id") == actor_id(user) and not snap.get("in_pool"))


async def create_native_activity(db, payload: dict, user: dict, perms: set) -> dict:
    if PERM_ACTIVITY_CREATE not in perms and PERM_OWNERSHIP_MANAGE not in perms:
        raise HTTPException(403, f"Permission '{PERM_ACTIVITY_CREATE}' diperlukan")
    outlet_id = str(payload.get("outlet_id") or "").strip()
    if not outlet_id:
        raise HTTPException(400, "outlet_id wajib")
    outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
    if not outlet:
        raise HTTPException(404, "Outlet tidak ditemukan")
    snap = await ownership_svc.get_ownership(db, outlet_id)
    if not _can_work_outlet(snap, user, perms):
        raise HTTPException(403, "Anda tidak berhak mencatat aktivitas outlet ini")
    if payload.get("sales_user_id") and str(payload.get("sales_user_id")) != actor_id(user) and PERM_OWNERSHIP_MANAGE not in perms:
        raise HTTPException(403, "Tidak boleh mencatat aktivitas atas nama sales lain")
    settings = await load_settings(db)
    types = activity_type_map(settings)
    type_id = str(payload.get("activity_type") or "").strip().lower()
    if type_id not in types or not types[type_id].get("active"):
        raise HTTPException(400, "Jenis aktivitas tidak valid")
    cfg = types[type_id]
    evidence_type = str(payload.get("evidence_type") or "").strip()
    evidence_link = str(payload.get("evidence_link") or "").strip()
    visit_photo_ids = [str(pid).strip() for pid in (payload.get("visit_photo_ids") or []) if str(pid).strip()]
    pending_photos = []
    if visit_photo_ids:
        pending_photos = await photo_svc.load_owned_photos(
            db, photo_ids=visit_photo_ids, outlet_id=outlet_id, user=user,
        )
    visit_requires_photo = type_id == "visit" and (cfg.get("visit_photo_required") or cfg.get("evidence_required"))
    if visit_requires_photo:
        if not pending_photos:
            raise HTTPException(400, "Foto Visit wajib untuk jenis aktivitas ini")
        if not any(p.get("location_status") == "OK" for p in pending_photos):
            raise HTTPException(400, "Lokasi visit wajib. Izinkan akses lokasi pada browser.")
    elif type_id != "visit" and cfg.get("evidence_required") and not (evidence_type or evidence_link):
        raise HTTPException(400, "Evidence wajib untuk jenis aktivitas ini")
    follow_raw = payload.get("next_follow_up_at") or payload.get("next_follow_up")
    follow = parse_dt(follow_raw)
    if cfg.get("next_follow_up_required") and not follow:
        raise HTTPException(400, "Next follow-up wajib untuk jenis aktivitas ini")
    activity_at = parse_dt(payload.get("activity_at") or payload.get("activity_date")) or None
    now = iso_now()
    if activity_at is None:
        activity_at_iso = now
        activity_at = parse_dt(now)
    else:
        activity_at_iso = activity_at.isoformat()
    actor = {"id": actor_id(user), "name": actor_name(user)}
    doc = stamp_created({
        "id": new_id(),
        "outlet_id": outlet_id,
        "activity_at": activity_at_iso,
        "sales_user_id": actor["id"],
        "sales_name": actor["name"],
        "activity_type": type_id,
        "result": str(payload.get("result") or payload.get("hasil") or "").strip(),
        "next_action": str(payload.get("next_action") or "").strip(),
        "next_follow_up_at": follow.isoformat() if follow else None,
        "notes": str(payload.get("notes") or "").strip(),
        "evidence_type": evidence_type,
        "evidence_link": evidence_link,
        "visit_photo_ids": [],
        "visit_photo_count": 0,
        "has_visit_location": False,
        "source": SOURCE_NATIVE,
        "import_batch_id": None,
        "source_row": None,
        "source_identity": None,
        "created_by_user_id": actor["id"],
        "counts_as_valid": bool(cfg.get("counts_as_valid")),
        "resets_inactivity": bool(cfg.get("resets_inactivity")),
    })
    await db[COL_ACTIVITIES].insert_one(doc)
    if visit_photo_ids:
        await photo_svc.claim_pending_photos(
            db,
            photo_ids=visit_photo_ids,
            outlet_id=outlet_id,
            activity_id=doc["id"],
            user=user,
        )
    doc = await db[COL_ACTIVITIES].find_one({"id": doc["id"]}, {"_id": 0}) or doc

    timer_applied = False
    if snap and snap.get("automation_status") == AUTOMATION_ACTIVE and not snap.get("in_pool"):
        rule = select_rule(settings, outlet)
        last_activity = activity_at_iso
        patch = {
            "last_activity_at": last_activity,
            "next_follow_up_at": doc["next_follow_up_at"] or snap.get("next_follow_up_at"),
        }
        should_reset = bool(cfg.get("counts_as_valid") and cfg.get("resets_inactivity") and rule.get("valid_activity_resets_inactivity"))
        if should_reset:
            windows = compute_windows(snap.get("ownership_started_at") or last_activity, last_activity, rule)
            patch.update({
                "timer_anchor_at": windows["timer_anchor_at"],
                "eligible_at": windows["eligible_at"],
                "warning_at": windows["warning_at"],
                "inactivity_eligible_at": windows["inactivity_eligible_at"],
            })
            timer_applied = True
        updated = await ownership_svc.cas_update(db, outlet_id, snap["version"], patch)
        if updated and timer_applied:
            await event_store.append_event(
                db,
                outlet_id=outlet_id,
                event_type=EVENT_ACTIVITY_TIMER,
                previous_owner_id=snap.get("current_owner_id"),
                new_owner_id=snap.get("current_owner_id"),
                actor_type="USER",
                actor_user_id=actor["id"],
                rule_id=rule.get("rule_id"),
                rule_version=rule.get("rule_version"),
                reason="Valid activity reset inactivity timer",
                source=SOURCE_NATIVE,
                correlation_key=f"activity-timer:{doc['id']}",
            )
            snap = updated
        elif updated:
            snap = updated
    enriched = (await photo_svc.attach_photos_to_activities(db, [doc]))[0]
    return {"activity": public_doc(enriched), "ownership": public_doc(snap), "timer_applied": timer_applied}


async def list_activities(db, user: dict, perms: set, *, outlet_id: str = "", page: int = 1, page_size: int = 50) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    query = {}
    if outlet_id:
        query["outlet_id"] = outlet_id
    if PERM_OWNERSHIP_MANAGE not in perms:
        query["sales_user_id"] = actor_id(user)
    total = await db[COL_ACTIVITIES].count_documents(query)
    rows = await db[COL_ACTIVITIES].find(query, {"_id": 0}).sort("activity_at", -1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    items = await photo_svc.attach_photos_to_activities(db, rows)
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size),
    }


async def get_activity(db, activity_id: str, user: dict, perms: set) -> dict:
    doc = await db[COL_ACTIVITIES].find_one({"id": activity_id}, {"_id": 0})
    if not doc:
        raise HTTPException(404, "Aktivitas tidak ditemukan")
    if PERM_OWNERSHIP_MANAGE not in perms and doc.get("sales_user_id") != actor_id(user):
        snap = await ownership_svc.get_ownership(db, doc.get("outlet_id"))
        if not snap or snap.get("current_owner_id") != actor_id(user):
            raise HTTPException(403, "Anda tidak berhak melihat aktivitas ini")
    enriched = (await photo_svc.attach_photos_to_activities(db, [doc]))[0]
    return {"activity": public_doc(enriched)}
