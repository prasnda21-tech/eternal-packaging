"""Outlet / prospect master."""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException

from services.sales_activity import ownership as ownership_svc
from services.sales_activity.clock import iso_now
from services.sales_activity.constants import (
    AUTOMATION_ACTIVE,
    COL_ACTIVITIES,
    COL_OWNERSHIP,
    COL_OUTLETS,
    KIND_PROSPECT,
    PERM_OUTLET_CREATE,
    PERM_OWNERSHIP_MANAGE,
    SOURCE_NATIVE,
)
from services.sales_activity.orders import last_order_at, last_valid_sales_order
from services.sales_activity.settings import load_settings
from services.sales_activity.users import get_user, public_user
from services.sales_activity.util import (
    actor_id,
    actor_name,
    is_blank,
    new_id,
    normalize_text,
    normalize_whatsapp,
    public_doc,
    stamp_created,
    strong_text,
)


def _identity_keys(payload: dict) -> dict:
    return {
        "outlet_code": strong_text(payload.get("outlet_code")),
        "whatsapp_norm": normalize_whatsapp(payload.get("whatsapp")),
    }


async def find_strong_duplicate(db, keys: dict, exclude_id: Optional[str] = None) -> Optional[dict]:
    clauses = []
    if keys.get("outlet_code"):
        clauses.append({"identity_keys.outlet_code": keys["outlet_code"]})
    if keys.get("whatsapp_norm"):
        clauses.append({"identity_keys.whatsapp_norm": keys["whatsapp_norm"]})
    if not clauses:
        return None
    query = {"$or": clauses}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    return await db[COL_OUTLETS].find_one(query, {"_id": 0})


async def fuzzy_name_warnings(db, name: str, exclude_id: Optional[str] = None) -> list:
    needle = normalize_text(name)
    if not needle or len(needle) < 4:
        return []
    query = {}
    if exclude_id:
        query["id"] = {"$ne": exclude_id}
    rows = await db[COL_OUTLETS].find(query, {"_id": 0, "id": 1, "name": 1, "address": 1}).to_list(500)
    warnings = []
    for row in rows:
        other = normalize_text(row.get("name"))
        if other and other == needle:
            warnings.append({"outlet_id": row["id"], "name": row.get("name"), "reason": "NAME_SIMILAR"})
    return warnings[:10]


def can_create_outlet(perms: set) -> bool:
    return PERM_OUTLET_CREATE in perms or PERM_OWNERSHIP_MANAGE in perms


async def create_native_outlet(db, payload: dict, user: dict, perms: set) -> dict:
    if not can_create_outlet(perms):
        raise HTTPException(403, f"Permission '{PERM_OUTLET_CREATE}' diperlukan")
    name = strong_text(payload.get("name") or payload.get("nama_outlet"))
    if not name:
        raise HTTPException(400, "Nama outlet wajib")
    requested_owner = strong_text(payload.get("owner_id"))
    actor = {"id": actor_id(user), "name": actor_name(user), "email": user.get("email"), "role": user.get("role")}
    if not actor["id"]:
        raise HTTPException(400, "User tidak memiliki id")
    if requested_owner and requested_owner != actor["id"]:
        if PERM_OWNERSHIP_MANAGE not in perms:
            raise HTTPException(403, "Tidak boleh mengklaim outlet milik sales lain")
        owner = await get_user(db, requested_owner)
        if not owner:
            raise HTTPException(400, "owner_id tidak ditemukan")
    else:
        owner = {
            "id": actor["id"],
            "name": actor["name"],
            "email": user.get("email"),
            "role": user.get("role"),
            "status": "active",
        }
    keys = _identity_keys(payload)
    dup = await find_strong_duplicate(db, keys)
    if dup:
        snap = await ownership_svc.get_ownership(db, dup["id"])
        if snap and snap.get("current_owner_id") and snap.get("current_owner_id") != owner["id"] and not snap.get("in_pool"):
            raise HTTPException(409, "Outlet ini sudah dimiliki sales lain")
        raise HTTPException(409, "Outlet dengan identitas yang sama sudah ada")
    warnings = await fuzzy_name_warnings(db, name)
    doc = stamp_created({
        "id": new_id(),
        "outlet_code": keys.get("outlet_code"),
        "name": name,
        "address": str(payload.get("address") or payload.get("alamat") or "").strip(),
        "maps_url": str(payload.get("maps_url") or payload.get("maps") or "").strip(),
        "instagram": str(payload.get("instagram") or payload.get("ig") or "").strip(),
        "marketplace": str(payload.get("marketplace") or "").strip(),
        "category": str(payload.get("category") or payload.get("kategori") or "").strip(),
        "pic_name": str(payload.get("pic_name") or payload.get("pic_outlet") or "").strip(),
        "pic_title": str(payload.get("pic_title") or payload.get("jabatan") or "").strip(),
        "whatsapp": str(payload.get("whatsapp") or "").strip(),
        "lead_source": str(payload.get("lead_source") or payload.get("sumber_lead") or "").strip(),
        "source": SOURCE_NATIVE,
        "import_batch_id": None,
        "source_row": None,
        "source_identity": None,
        "linked_customer_name": name,
        "linked_outlet_name": name,
        "linked_customer_id": strong_text(payload.get("customer_id")),
        "customer_kind": KIND_PROSPECT,
        "created_by_user_id": actor["id"],
        "identity_keys": keys,
    })
    await db[COL_OUTLETS].insert_one(doc)
    settings = await load_settings(db)
    snap = await ownership_svc.start_native_cycle(db, doc, owner, settings, actor)
    return {"outlet": public_doc(doc), "ownership": public_doc(snap), "warnings": warnings}


def _scope_query(user: dict, perms: set, owner_id: Optional[str]) -> dict:
    query = {}
    manage = PERM_OWNERSHIP_MANAGE in perms
    if owner_id:
        query["current_owner_id"] = owner_id
        return query
    if not manage:
        query["current_owner_id"] = actor_id(user)
    return query


async def list_outlets(db, user: dict, perms: set, *, q: str = "", owner_id: str = "", automation_status: str = "", customer_kind: str = "", in_pool: Optional[bool] = None, page: int = 1, page_size: int = 50) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    own_query = _scope_query(user, perms, strong_text(owner_id))
    snap_filter = dict(own_query)
    if automation_status:
        snap_filter["automation_status"] = automation_status
    if customer_kind:
        snap_filter["customer_kind"] = customer_kind
    if in_pool is True:
        snap_filter["in_pool"] = True
    if in_pool is False:
        snap_filter["in_pool"] = {"$ne": True}

    outlet_query = {}
    if q and q.strip():
        needle = re.escape(q.strip())
        outlet_query["$or"] = [
            {"name": {"$regex": needle, "$options": "i"}},
            {"address": {"$regex": needle, "$options": "i"}},
            {"outlet_code": {"$regex": needle, "$options": "i"}},
            {"whatsapp": {"$regex": needle, "$options": "i"}},
            {"pic_name": {"$regex": needle, "$options": "i"}},
        ]
    snaps = {}
    async for snap in db[COL_OWNERSHIP].find(snap_filter, {"_id": 0}):
        snaps[snap["outlet_id"]] = snap
    if snap_filter:
        outlet_query["id"] = {"$in": list(snaps.keys()) or ["__none__"]}
    total = await db[COL_OUTLETS].count_documents(outlet_query)
    rows = await db[COL_OUTLETS].find(outlet_query, {"_id": 0}).sort("updated_at", -1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    items = []
    for row in rows:
        snap = snaps.get(row["id"]) or await ownership_svc.get_ownership(db, row["id"])
        items.append({**row, "ownership": snap})
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size),
    }


async def get_outlet_detail(db, outlet_id: str, user: dict, perms: set) -> dict:
    outlet = await db[COL_OUTLETS].find_one({"id": outlet_id}, {"_id": 0})
    if not outlet:
        raise HTTPException(404, "Outlet tidak ditemukan")
    snap = await ownership_svc.get_ownership(db, outlet_id)
    if PERM_OWNERSHIP_MANAGE not in perms:
        if not snap or snap.get("current_owner_id") != actor_id(user):
            raise HTTPException(403, "Anda tidak berhak melihat outlet ini")
    from services.sales_activity import events as event_store
    from services.sales_activity import photos as photo_svc
    activities = await db[COL_ACTIVITIES].find({"outlet_id": outlet_id}, {"_id": 0}).sort("activity_at", -1).to_list(500)
    activities = await photo_svc.attach_photos_to_activities(db, activities)
    history = await event_store.list_events(db, outlet_id)
    last_order = await last_valid_sales_order(
        db,
        customer_name=outlet.get("linked_customer_name") or outlet.get("name"),
        outlet_name=outlet.get("linked_outlet_name") or outlet.get("name"),
        customer_id=outlet.get("linked_customer_id") or "",
    )
    last_order_summary = None
    if last_order:
        at = last_order_at(last_order)
        last_order_summary = {
            "po_number": last_order.get("po_number"),
            "id": last_order.get("id"),
            "customer": last_order.get("customer"),
            "outlet": last_order.get("outlet"),
            "order_date": at.isoformat() if at else last_order.get("order_date"),
        }
    return {
        "outlet": outlet,
        "ownership": snap,
        "activities": activities,
        "history": history,
        "last_valid_order": last_order_summary,
    }
