"""Room and membership helpers for Internal Work Chat."""
from __future__ import annotations

import uuid
from typing import Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.chat_constants import COLL_MEMBERS, COLL_MESSAGES, COLL_ROOMS, ROOM_CUSTOM_GROUP, ROOM_DM, ROOM_SYSTEM_GROUP, ROOM_WORK
from services.chat_work import work_key as make_work_key
from services.purchase_common import now_iso


def user_id(user: dict) -> str:
    return str((user or {}).get("user_id") or (user or {}).get("id") or "").strip()


def dm_key(uid_a, uid_b) -> str:
    a, b = sorted([str(uid_a), str(uid_b)])
    return f"{a}:{b}"


def _actor(user: dict) -> dict:
    return {"id": user_id(user), "name": (user or {}).get("name"), "email": (user or {}).get("email"), "role": (user or {}).get("role")}


async def _active_user(db, uid: str) -> Optional[dict]:
    return await db.users.find_one({"id": str(uid), "status": "active"}, {"password_hash": 0, "_id": 0})


async def _upsert_members(db, room_id: str, member_ids: list[str], actor: dict | None = None) -> None:
    now = now_iso()
    seen = set()
    for uid in member_ids:
        uid = str(uid or "").strip()
        if not uid or uid in seen:
            continue
        seen.add(uid)
        u = await _active_user(db, uid)
        if not u:
            raise HTTPException(400, "Anggota chat tidak aktif atau tidak ditemukan")
        await db[COLL_MEMBERS].update_one(
            {"room_id": room_id, "user_id": uid},
            {
                "$set": {
                    "active": True,
                    "left_at": None,
                    "updated_at": now,
                    "user_name": u.get("name") or u.get("email"),
                    "user_email": u.get("email"),
                    "user_role": u.get("role"),
                },
                "$setOnInsert": {
                    "id": str(uuid.uuid4()),
                    "room_id": room_id,
                    "user_id": uid,
                    "joined_at": now,
                    "last_read_sequence": 0,
                    "added_by": (actor or {}).get("id"),
                },
            },
            upsert=True,
        )


async def require_active_member(db, room_id, uid) -> dict:
    member = await db[COLL_MEMBERS].find_one({"room_id": str(room_id), "user_id": str(uid), "active": True}, {"_id": 0})
    if not member:
        raise HTTPException(403, "Anda bukan anggota room chat ini")
    return member


async def get_or_create_dm(db, user_a: dict, user_b) -> dict:
    uid_a = user_id(user_a)
    uid_b = user_id(user_b) if isinstance(user_b, dict) else str(user_b)
    if not uid_a or not uid_b:
        raise HTTPException(400, "User tidak valid")
    if uid_a == uid_b:
        raise HTTPException(400, "Tidak dapat membuat DM ke diri sendiri")
    peer = await _active_user(db, uid_b)
    if not peer:
        raise HTTPException(404, "User tidak ditemukan atau tidak aktif")
    key = dm_key(uid_a, uid_b)
    now = now_iso()
    existing = await db[COLL_ROOMS].find_one({"type": ROOM_DM, "dm_key": key}, {"_id": 0})
    if existing:
        await _upsert_members(db, existing["id"], [uid_a, uid_b], _actor(user_a))
        return existing
    doc = {
        "id": str(uuid.uuid4()),
        "type": ROOM_DM,
        "dm_key": key,
        "name": None,
        "participant_ids": sorted([uid_a, uid_b]),
        "archived": False,
        "last_sequence": 0,
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
        "created_by": uid_a,
    }
    try:
        await db[COLL_ROOMS].insert_one(doc)
    except DuplicateKeyError:
        doc = await db[COLL_ROOMS].find_one({"type": ROOM_DM, "dm_key": key}, {"_id": 0})
    await _upsert_members(db, doc["id"], [uid_a, uid_b], _actor(user_a))
    return {k: v for k, v in doc.items() if k != "_id"}


async def create_custom_group(db, creator: dict, name: str, member_ids: list[str]) -> dict:
    title = str(name or "").strip()
    if not title:
        raise HTTPException(400, "Nama grup wajib diisi")
    now = now_iso()
    rid = str(uuid.uuid4())
    doc = {
        "id": rid,
        "type": ROOM_CUSTOM_GROUP,
        "name": title,
        "archived": False,
        "last_sequence": 0,
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
        "created_by": user_id(creator),
    }
    await db[COLL_ROOMS].insert_one(doc)
    ids = [user_id(creator)] + [str(x) for x in (member_ids or [])]
    await _upsert_members(db, rid, ids, _actor(creator))
    return {k: v for k, v in doc.items() if k != "_id"}


async def create_or_get_work_room(db, creator: dict, work_context: dict, member_ids: list[str] | None = None) -> dict:
    key = make_work_key(
        work_context.get("order_id"),
        line_id=work_context.get("line_id"),
        line_idx=work_context.get("line_idx"),
        task_id=work_context.get("task_id"),
    )
    now = now_iso()
    doc = {
        "id": str(uuid.uuid4()),
        "type": ROOM_WORK,
        "work_key": key,
        "name": work_context.get("po_number") or key,
        "work_context": dict(work_context),
        "archived": False,
        "last_sequence": 0,
        "created_at": now,
        "updated_at": now,
        "last_activity_at": now,
        "created_by": user_id(creator),
    }
    existing = await db[COLL_ROOMS].find_one({"type": ROOM_WORK, "work_key": key}, {"_id": 0})
    if existing:
        await _upsert_members(db, existing["id"], [user_id(creator)] + list(member_ids or []), _actor(creator))
        return existing
    try:
        await db[COLL_ROOMS].insert_one(doc)
    except DuplicateKeyError:
        doc = await db[COLL_ROOMS].find_one({"type": ROOM_WORK, "work_key": key}, {"_id": 0})
    await _upsert_members(db, doc["id"], [user_id(creator)] + list(member_ids or []), _actor(creator))
    return {k: v for k, v in doc.items() if k != "_id"}


async def _bulk_users_by_id(db, user_ids: list[str]) -> dict[str, dict]:
    ids = [str(x) for x in user_ids if x]
    if not ids:
        return {}
    users = await db.users.find(
        {"id": {"$in": ids}},
        {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1},
    ).to_list(len(ids))
    return {str(u["id"]): u for u in users if u.get("id")}


def _member_display_fields(member: dict, user: dict | None = None) -> dict:
    """Presentation fields for room members. Canonical identity remains user_id."""
    u = user or {}
    uid = str(member.get("user_id") or "")
    display = (
        (u.get("name") or "").strip()
        or (member.get("user_name") or "").strip()
        or (member.get("name") or "").strip()
        or (u.get("email") or "").strip()
        or (member.get("user_email") or "").strip()
        or "Pengguna"
    )
    role = u.get("role") or member.get("user_role") or member.get("role")
    email = u.get("email") or member.get("user_email") or member.get("email")
    return {
        **member,
        "user_id": uid,
        "name": display,
        "display_name": display,
        "role": role,
        "email": email,
    }


async def enrich_room_members(db, members: list[dict]) -> list[dict]:
    by_id = await _bulk_users_by_id(db, [m.get("user_id") for m in members or []])
    return [_member_display_fields(m, by_id.get(str(m.get("user_id") or ""))) for m in (members or [])]


async def apply_room_display_name(db, room: dict, viewer_uid: str, users_by_id: dict[str, dict] | None = None) -> dict:
    """Derive presentation title for DMs; leave system/custom/work titles intact."""
    out = dict(room)
    display_name = out.get("name")
    if out.get("type") == ROOM_DM:
        peer_id = next((p for p in (out.get("participant_ids") or []) if p != viewer_uid), None)
        if peer_id:
            peer = (users_by_id or {}).get(str(peer_id))
            if peer is None and users_by_id is None:
                peer = await db.users.find_one({"id": peer_id}, {"_id": 0, "name": 1, "email": 1})
            display_name = (peer or {}).get("name") or (peer or {}).get("email") or "DM"
    out["room_id"] = out.get("room_id") or out.get("id")
    out["room_type"] = out.get("room_type") or out.get("type")
    out["name"] = display_name or out.get("system_key") or out.get("work_key") or "Chat"
    return out


async def room_detail(db, room_id: str, uid: str) -> dict:
    await require_active_member(db, room_id, uid)
    room = await db[COLL_ROOMS].find_one({"id": str(room_id)}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room chat tidak ditemukan")
    members = await db[COLL_MEMBERS].find({"room_id": room_id, "active": True}, {"_id": 0}).to_list(200)
    member_ids = [m.get("user_id") for m in members]
    peer_ids = list(room.get("participant_ids") or [])
    users_by_id = await _bulk_users_by_id(db, [*member_ids, *peer_ids])
    room = await apply_room_display_name(db, room, uid, users_by_id=users_by_id)
    room["members"] = [_member_display_fields(m, users_by_id.get(str(m.get("user_id") or ""))) for m in members]
    return room


async def list_my_rooms(db, user: dict, filter_type: Optional[str] = None) -> list[dict]:
    uid = user_id(user)
    room_type = None if filter_type in (None, "", "all", "Semua") else filter_type
    if room_type in ("personal", "Personal"):
        room_type = ROOM_DM
    if room_type in ("work", "Pekerjaan"):
        room_type = ROOM_WORK
    if room_type in ("Grup",):
        room_type = "group"
    members = await db[COLL_MEMBERS].find({"user_id": uid, "active": True}, {"_id": 0}).to_list(1000)
    by_room = {m["room_id"]: m for m in members}
    if not by_room:
        return []
    q = {"id": {"$in": list(by_room)}}
    if room_type == "group":
        q["type"] = {"$in": [ROOM_CUSTOM_GROUP, ROOM_SYSTEM_GROUP]}
    elif room_type:
        q["type"] = room_type
    rooms = await db[COLL_ROOMS].find(q, {"_id": 0}).sort("last_activity_at", -1).to_list(1000)
    peer_ids = []
    for room in rooms:
        if room.get("type") == ROOM_DM:
            peer_id = next((p for p in (room.get("participant_ids") or []) if p != uid), None)
            if peer_id:
                peer_ids.append(peer_id)
    users_by_id = await _bulk_users_by_id(db, peer_ids)
    out = []
    for room in rooms:
        member = by_room.get(room["id"], {})
        last_read = int(member.get("last_read_sequence") or 0)
        unread = max(0, int(room.get("last_sequence") or 0) - last_read)
        preview = await db[COLL_MESSAGES].find_one(
            {"room_id": room["id"]},
            {"_id": 0, "content": 1, "sender_user_id": 1, "sequence": 1, "deleted_at": 1, "created_at": 1},
            sort=[("sequence", -1)],
        )
        if preview and preview.get("deleted_at"):
            preview["content"] = None
            preview["deleted"] = True
        room = await apply_room_display_name(db, room, uid, users_by_id=users_by_id)
        room["member"] = member
        room["unread"] = unread
        room["unread_count"] = unread
        room["last_message"] = preview
        room["last_message_preview"] = (
            "Pesan dihapus" if preview and preview.get("deleted_at")
            else (preview or {}).get("content")
        )
        out.append(room)
    return out


async def unread_summary(db, user: dict) -> dict:
    rooms = await list_my_rooms(db, user)
    items = [{"room_id": r["id"], "unread": r.get("unread_count", 0)} for r in rooms if r.get("unread_count", 0) > 0]
    return {"total_unread": sum(i["unread"] for i in items), "rooms": items}


async def update_room(db, room_id: str, name: str, user: dict) -> dict:
    room = await db[COLL_ROOMS].find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room chat tidak ditemukan")
    if room.get("type") != ROOM_CUSTOM_GROUP:
        raise HTTPException(400, "Hanya custom group yang dapat diubah")
    await db[COLL_ROOMS].update_one({"id": room_id}, {"$set": {"name": str(name or "").strip(), "updated_at": now_iso()}})
    return await db[COLL_ROOMS].find_one({"id": room_id}, {"_id": 0})


async def add_members(db, room_id: str, user_ids: list[str], actor: dict) -> dict:
    room = await db[COLL_ROOMS].find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room chat tidak ditemukan")
    if room.get("type") not in (ROOM_CUSTOM_GROUP, ROOM_WORK):
        raise HTTPException(400, "Anggota room ini tidak dapat diubah manual")
    await _upsert_members(db, room_id, user_ids, _actor(actor))
    return await room_detail(db, room_id, user_id(actor))


async def remove_members(db, room_id: str, user_ids: list[str], actor: dict) -> dict:
    room = await db[COLL_ROOMS].find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room chat tidak ditemukan")
    if room.get("type") not in (ROOM_CUSTOM_GROUP, ROOM_WORK):
        raise HTTPException(400, "Anggota room ini tidak dapat diubah manual")
    await db[COLL_MEMBERS].update_many(
        {"room_id": room_id, "user_id": {"$in": [str(x) for x in user_ids]}},
        {"$set": {"active": False, "left_at": now_iso(), "updated_at": now_iso()}},
    )
    return await room_detail(db, room_id, user_id(actor))


async def archive_room(db, room_id: str, actor: dict) -> dict:
    room = await db[COLL_ROOMS].find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room chat tidak ditemukan")
    if room.get("type") not in (ROOM_CUSTOM_GROUP, ROOM_WORK):
        raise HTTPException(400, "Room ini tidak dapat diarsipkan")
    await db[COLL_ROOMS].update_one({"id": room_id}, {"$set": {"archived": True, "archived_at": now_iso(), "archived_by": user_id(actor), "updated_at": now_iso()}})
    return await db[COLL_ROOMS].find_one({"id": room_id}, {"_id": 0})
