"""System chat group membership synced from the single user role."""
from __future__ import annotations

import uuid

from permissions import ROLE_LABELS
from services.chat_constants import COLL_MEMBERS, COLL_ROOMS, ROOM_SYSTEM_GROUP
from services.purchase_common import now_iso


def system_key_for_role(role) -> str:
    return f"role:{str(role or '').strip()}"


def _user_id(user: dict) -> str:
    return str((user or {}).get("user_id") or (user or {}).get("id") or "").strip()


async def ensure_system_room_for_role(db, role, label=None) -> dict:
    role = str(role or "").strip()
    if not role:
        raise ValueError("role is required")
    key = system_key_for_role(role)
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "type": ROOM_SYSTEM_GROUP,
        "system_key": key,
        "role": role,
        "name": label or ROLE_LABELS.get(role) or role,
        "cannot_delete": True,
        "archived": False,
        "last_sequence": 0,
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
    }
    insert_doc = {k: v for k, v in doc.items() if k not in {"name", "cannot_delete", "archived", "updated_at"}}
    await db[COLL_ROOMS].update_one(
        {"type": ROOM_SYSTEM_GROUP, "system_key": key},
        {
            "$setOnInsert": insert_doc,
            "$set": {"name": doc["name"], "cannot_delete": True, "archived": False, "updated_at": now},
        },
        upsert=True,
    )
    return await db[COLL_ROOMS].find_one({"type": ROOM_SYSTEM_GROUP, "system_key": key}, {"_id": 0})


async def _upsert_member(db, room_id: str, user: dict, *, active: bool) -> None:
    uid = _user_id(user)
    if not uid:
        return
    now = now_iso()
    patch = {
        "user_id": uid,
        "room_id": room_id,
        "active": bool(active),
        "updated_at": now,
        "user_name": user.get("name") or user.get("email"),
        "user_email": user.get("email"),
        "user_role": user.get("role"),
    }
    if active:
        patch["left_at"] = None
        op = {"$set": patch, "$setOnInsert": {"id": str(uuid.uuid4()), "joined_at": now, "last_read_sequence": 0}}
    else:
        patch["left_at"] = now
        op = {"$set": patch, "$setOnInsert": {"id": str(uuid.uuid4()), "joined_at": now, "last_read_sequence": 0}}
    await db[COLL_MEMBERS].update_one({"room_id": room_id, "user_id": uid}, op, upsert=True)


async def sync_user_system_membership(db, user: dict) -> None:
    if not user:
        return
    uid = _user_id(user)
    if not uid:
        return
    status = user.get("status", "active")
    role = str(user.get("role") or "").strip()
    active = status == "active" and bool(role)
    target_room = None
    if active:
        target_room = await ensure_system_room_for_role(db, role)
        await _upsert_member(db, target_room["id"], user, active=True)
    cursor = db[COLL_MEMBERS].aggregate([
        {"$match": {"user_id": uid}},
        {"$lookup": {"from": COLL_ROOMS, "localField": "room_id", "foreignField": "id", "as": "room"}},
        {"$unwind": "$room"},
        {"$match": {"room.type": ROOM_SYSTEM_GROUP}},
    ])
    async for member in cursor:
        rid = member.get("room_id")
        if not active or not target_room or rid != target_room["id"]:
            await db[COLL_MEMBERS].update_one(
                {"room_id": rid, "user_id": uid},
                {"$set": {"active": False, "left_at": now_iso(), "updated_at": now_iso()}},
            )


async def bootstrap_system_groups(db) -> None:
    roles = set(ROLE_LABELS.keys())
    async for r in db.roles.find({}, {"role": 1, "label": 1, "name": 1, "_id": 0}):
        role = str(r.get("role") or "").strip()
        if role:
            roles.add(role)
            await ensure_system_room_for_role(db, role, r.get("label") or r.get("name"))
    for role in sorted(roles):
        await ensure_system_room_for_role(db, role)
    async for user in db.users.find({"status": "active"}, {"password_hash": 0}):
        await sync_user_system_membership(db, user)
    async for user in db.users.find({"status": {"$ne": "active"}}, {"password_hash": 0}):
        await sync_user_system_membership(db, user)


async def on_user_changed(db, user_id) -> None:
    user = await db.users.find_one({"id": str(user_id)}, {"password_hash": 0})
    if user:
        await sync_user_system_membership(db, user)
