"""Message persistence and read-state helpers."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.chat_constants import COLL_ATTACHMENTS, COLL_MEMBERS, COLL_MESSAGES, COLL_ROOMS, EDIT_WINDOW_SECONDS, MAX_ATTACHMENTS_PER_MESSAGE, ROOM_SYSTEM_GROUP
from services.chat_rooms import require_active_member, user_id
from services.chat_work import resolve_work_snapshot
from services.purchase_common import now_iso


def _parse_dt(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return datetime.now(timezone.utc)


def public_message(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    msg = {k: v for k, v in doc.items() if k != "_id"}
    msg["message_id"] = msg.get("id")
    if msg.get("deleted_at"):
        msg["content"] = None
        msg["deleted"] = True
        msg.pop("revisions", None)
    else:
        msg["deleted"] = False
    return msg


async def _validate_mentions(db, room: dict, mentions: list[dict] | None) -> list[dict]:
    out = []
    for m in mentions or []:
        typ = str(m.get("type") or "").strip()
        mid = str(m.get("id") or "").strip()
        if typ not in ("user", "role") or not mid:
            raise HTTPException(400, "Mention tidak valid")
        if typ == "user":
            member = await db[COLL_MEMBERS].find_one({"room_id": room["id"], "user_id": mid, "active": True}, {"_id": 0})
            if not member:
                raise HTTPException(400, "Mention user harus anggota room")
        if typ == "role":
            ok = False
            if room.get("type") == ROOM_SYSTEM_GROUP and room.get("role") == mid:
                ok = True
            if not ok:
                role_members = await db[COLL_MEMBERS].aggregate([
                    {"$match": {"room_id": room["id"], "active": True}},
                    {"$lookup": {"from": "users", "localField": "user_id", "foreignField": "id", "as": "user"}},
                    {"$unwind": "$user"},
                    {"$match": {"user.role": mid, "user.status": "active"}},
                    {"$limit": 1},
                ]).to_list(1)
                ok = bool(role_members)
            if not ok:
                raise HTTPException(400, "Mention role tidak sesuai anggota room")
        out.append({"type": typ, "id": mid, "label": str(m.get("label") or mid)})
    return out


async def _validate_attachments(db, room_id: str, attachment_ids: list[str] | None) -> list[dict]:
    ids = [str(x) for x in (attachment_ids or []) if str(x or "").strip()]
    if len(ids) > MAX_ATTACHMENTS_PER_MESSAGE:
        raise HTTPException(400, f"Maksimal {MAX_ATTACHMENTS_PER_MESSAGE} lampiran per pesan")
    out = []
    for aid in ids:
        rec = await db[COLL_ATTACHMENTS].find_one({"attachment_id": aid, "room_id": room_id}, {"_id": 0})
        if not rec:
            raise HTTPException(400, "Lampiran tidak ditemukan untuk room ini")
        out.append({k: rec.get(k) for k in ("attachment_id", "id", "original_filename", "content_type", "size", "uploaded_at", "uploaded_by")})
    return out


async def send_message(db, room_id: str, sender: dict, *, content: str = "", client_message_id: Optional[str] = None, mentions=None, work_refs=None, attachment_ids=None) -> dict:
    uid = user_id(sender)
    await require_active_member(db, room_id, uid)
    room = await db[COLL_ROOMS].find_one({"id": room_id}, {"_id": 0})
    if not room:
        raise HTTPException(404, "Room chat tidak ditemukan")
    if room.get("archived"):
        raise HTTPException(400, "Room chat sudah diarsipkan")
    if client_message_id:
        existing = await db[COLL_MESSAGES].find_one({"sender_user_id": uid, "client_message_id": str(client_message_id)}, {"_id": 0})
        if existing:
            return public_message(existing)
    clean_content = str(content or "").strip()
    if not clean_content and not attachment_ids and not work_refs:
        raise HTTPException(400, "Pesan tidak boleh kosong")
    clean_mentions = await _validate_mentions(db, room, mentions)
    snapshots = []
    for ref in work_refs or []:
        if not isinstance(ref, dict):
            raise HTTPException(400, "Referensi pekerjaan tidak valid")
        snapshots.append(await resolve_work_snapshot(
            db,
            sender,
            order_id=ref.get("order_id"),
            line_id=ref.get("line_id"),
            line_idx=ref.get("line_idx"),
            task_id=ref.get("task_id"),
        ))
    attachments = await _validate_attachments(db, room_id, attachment_ids)
    now = now_iso()
    seq_doc = await db[COLL_ROOMS].find_one_and_update(
        {"id": room_id},
        {"$inc": {"last_sequence": 1}, "$set": {"last_activity_at": now, "updated_at": now}},
        return_document=ReturnDocument.AFTER,
        projection={"_id": 0, "last_sequence": 1},
    )
    sequence = int((seq_doc or {}).get("last_sequence") or 1)
    doc = {
        "id": str(uuid.uuid4()),
        "room_id": room_id,
        "sequence": sequence,
        "sender_user_id": uid,
        "sender_name": sender.get("name") or sender.get("email"),
        "sender_email": sender.get("email"),
        "content": clean_content,
        "client_message_id": str(client_message_id) if client_message_id else None,
        "mentions": clean_mentions,
        "work_refs": snapshots,
        "attachments": attachments,
        "created_at": now,
        "updated_at": now,
        "edited_at": None,
        "deleted_at": None,
    }
    try:
        await db[COLL_MESSAGES].insert_one(doc)
    except DuplicateKeyError:
        if client_message_id:
            existing = await db[COLL_MESSAGES].find_one({"sender_user_id": uid, "client_message_id": str(client_message_id)}, {"_id": 0})
            if existing:
                return public_message(existing)
        raise
    await db[COLL_ATTACHMENTS].update_many({"attachment_id": {"$in": [a["attachment_id"] for a in attachments]}}, {"$set": {"message_id": doc["id"], "updated_at": now}})
    await db[COLL_MEMBERS].update_one({"room_id": room_id, "user_id": uid}, {"$max": {"last_read_sequence": sequence}, "$set": {"updated_at": now}})
    return public_message(doc)


async def list_messages(db, room_id: str, uid: str, *, before_sequence=None, limit=None) -> list[dict]:
    await require_active_member(db, room_id, uid)
    lim = max(1, min(int(limit or 40), 100))
    q = {"room_id": room_id}
    if before_sequence is not None and str(before_sequence).strip() != "":
        q["sequence"] = {"$lt": int(before_sequence)}
    docs = await db[COLL_MESSAGES].find(q, {"_id": 0}).sort("sequence", -1).limit(lim).to_list(lim)
    return [public_message(d) for d in reversed(docs)]


async def edit_message(db, message_id: str, editor: dict, content: str) -> dict:
    uid = user_id(editor)
    msg = await db[COLL_MESSAGES].find_one({"id": message_id}, {"_id": 0})
    if not msg:
        raise HTTPException(404, "Pesan tidak ditemukan")
    await require_active_member(db, msg["room_id"], uid)
    if msg.get("sender_user_id") != uid:
        raise HTTPException(403, "Hanya pengirim yang dapat mengubah pesan")
    if msg.get("deleted_at"):
        raise HTTPException(400, "Pesan sudah dihapus")
    age = (datetime.now(timezone.utc) - _parse_dt(msg.get("created_at"))).total_seconds()
    if age > EDIT_WINDOW_SECONDS:
        raise HTTPException(400, "Batas waktu edit pesan sudah lewat")
    now = now_iso()
    rev = {"content": msg.get("content"), "edited_at": now, "editor_user_id": uid}
    await db[COLL_MESSAGES].update_one(
        {"id": message_id},
        {"$set": {"content": str(content or "").strip(), "edited_at": now, "updated_at": now}, "$push": {"revisions": rev}},
    )
    return public_message(await db[COLL_MESSAGES].find_one({"id": message_id}, {"_id": 0}))


async def delete_message(db, message_id: str, actor: dict, *, can_moderate: bool = False) -> dict:
    uid = user_id(actor)
    msg = await db[COLL_MESSAGES].find_one({"id": message_id}, {"_id": 0})
    if not msg:
        raise HTTPException(404, "Pesan tidak ditemukan")
    await require_active_member(db, msg["room_id"], uid)
    if msg.get("sender_user_id") != uid and not can_moderate:
        raise HTTPException(403, "Anda tidak dapat menghapus pesan ini")
    if not msg.get("deleted_at"):
        await db[COLL_MESSAGES].update_one(
            {"id": message_id},
            {"$set": {"deleted_at": now_iso(), "deleted_by": uid, "updated_at": now_iso()}},
        )
    return public_message(await db[COLL_MESSAGES].find_one({"id": message_id}, {"_id": 0}))


async def mark_read(db, room_id: str, uid: str, sequence: int) -> dict:
    await require_active_member(db, room_id, uid)
    seq = max(0, int(sequence or 0))
    await db[COLL_MEMBERS].update_one(
        {"room_id": room_id, "user_id": uid},
        {"$max": {"last_read_sequence": seq}, "$set": {"updated_at": now_iso()}},
    )
    member = await db[COLL_MEMBERS].find_one({"room_id": room_id, "user_id": uid}, {"_id": 0})
    return {"room_id": room_id, "user_id": uid, "last_read_sequence": int(member.get("last_read_sequence") or 0)}
