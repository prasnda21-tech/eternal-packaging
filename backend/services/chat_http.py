"""HTTP and WebSocket routes for Internal Work Chat V1."""
from __future__ import annotations

import os
from typing import Optional

import jwt
from fastapi import HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field

from permissions import effective_permissions
from services import chat_attachments, chat_messages, chat_realtime, chat_rooms, chat_work
from services.chat_constants import PERM_CHAT_GROUP_MANAGE, PERM_CHAT_MODERATE, PERM_CHAT_USE
from services.chat_indexes import ensure_indexes


class DmPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    peer_user_id: str = Field(..., min_length=1)


class CustomRoomPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    member_ids: list[str] = Field(default_factory=list)


class RoomPatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None


class MembersPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_ids: list[str] = Field(default_factory=list)


class WorkRoomPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_id: str = Field(..., min_length=1)
    line_id: Optional[str] = None
    line_idx: Optional[int] = None
    task_id: Optional[str] = None
    member_ids: list[str] = Field(default_factory=list)


class MessagePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = ""
    client_message_id: Optional[str] = None
    mentions: list[dict] = Field(default_factory=list)
    work_refs: list[dict] = Field(default_factory=list)
    attachment_ids: list[str] = Field(default_factory=list)


class EditPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(..., min_length=1)


class ReadPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sequence: int = 0


class DataUrlAttachmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_url: str = Field(..., min_length=1)
    filename: str = "lampiran"


def _dump(payload) -> dict:
    return payload.model_dump(exclude_unset=True) if hasattr(payload, "model_dump") else payload.dict(exclude_unset=True)


def _uid(user: dict) -> str:
    return chat_rooms.user_id(user)


async def _has_perm(user_permissions, user: dict, perm: str) -> bool:
    if user.get("role") == "admin":
        return True
    if user_permissions:
        return perm in await user_permissions(user)
    return perm in effective_permissions(user.get("role"), user.get("permissions_override") or {})


async def _broadcast_room_membership(db, room_id: str, event: dict, *, also_user_ids: list[str] | None = None) -> None:
    await chat_realtime.broadcast_to_room(room_id, event)
    notify_ids = {str(m["user_id"]) for m in await db.chat_members.find(
        {"room_id": room_id, "active": True}, {"_id": 0, "user_id": 1}
    ).to_list(500)}
    for uid in also_user_ids or []:
        if uid:
            notify_ids.add(str(uid))
    for uid in notify_ids:
        await chat_realtime.broadcast_to_user(uid, event)


async def authenticate_ws(websocket: WebSocket, db, user_permissions=None) -> dict:
    auth = websocket.cookies.get("access_token") or websocket.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        auth = auth[7:]
    if not auth:
        auth = websocket.query_params.get("auth", "")
    if not auth:
        await websocket.close(code=4401)
        raise HTTPException(401, "Silakan masuk terlebih dahulu")
    try:
        payload = jwt.decode(auth, os.environ["JWT_SECRET"], algorithms=["HS256"])
    except jwt.InvalidTokenError as exc:
        await websocket.close(code=4401)
        raise HTTPException(401, "Sesi berakhir, silakan masuk kembali") from exc
    user = None
    if payload.get("email"):
        user = await db.users.find_one({"email": payload["email"]}, {"password_hash": 0, "_id": 0})
    if not user and payload.get("sub"):
        user = await db.users.find_one({"id": payload["sub"]}, {"password_hash": 0, "_id": 0})
    if not user or user.get("status", "active") != "active":
        await websocket.close(code=4403)
        raise HTTPException(403, "Akun dinonaktifkan")
    if not await _has_perm(user_permissions, user, PERM_CHAT_USE):
        await websocket.close(code=4403)
        raise HTTPException(403, "Permission chat.use diperlukan")
    user["user_id"] = user.get("id")
    return user


def register_chat_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
    user_permissions=None,
):
    @api_router.get("/chat/unread-summary")
    async def get_unread_summary(user=Depends(require_perm(PERM_CHAT_USE))):
        return await chat_rooms.unread_summary(get_db(), user)

    @api_router.get("/chat/rooms")
    async def get_rooms(filter: str = Query("all"), user=Depends(require_perm(PERM_CHAT_USE))):
        return {"rooms": await chat_rooms.list_my_rooms(get_db(), user, filter_type=filter)}

    @api_router.post("/chat/rooms/dm", status_code=201)
    async def post_dm(payload: DmPayload, user=Depends(require_perm(PERM_CHAT_USE))):
        db = get_db()
        await ensure_indexes(db)
        return await chat_rooms.get_or_create_dm(db, user, payload.peer_user_id)

    @api_router.post("/chat/rooms/custom", status_code=201)
    async def post_custom(payload: CustomRoomPayload, user=Depends(require_perm(PERM_CHAT_USE, PERM_CHAT_GROUP_MANAGE))):
        db = get_db()
        await ensure_indexes(db)
        room = await chat_rooms.create_custom_group(db, user, payload.name, payload.member_ids)
        await _broadcast_room_membership(db, room["id"], {"type": chat_realtime.EVENT_MEMBERSHIP_UPDATED, "room_id": room["id"]})
        return room

    @api_router.patch("/chat/rooms/{room_id}")
    async def patch_room(room_id: str, payload: RoomPatchPayload, user=Depends(require_perm(PERM_CHAT_USE, PERM_CHAT_GROUP_MANAGE))):
        room = await chat_rooms.update_room(get_db(), room_id, payload.name or "", user)
        await chat_realtime.broadcast_to_room(room_id, {"type": chat_realtime.EVENT_ROOM_UPDATED, "room": room})
        return room

    @api_router.post("/chat/rooms/{room_id}/members")
    async def post_members(room_id: str, payload: MembersPayload, user=Depends(require_perm(PERM_CHAT_USE, PERM_CHAT_GROUP_MANAGE))):
        db = get_db()
        room = await chat_rooms.add_members(db, room_id, payload.user_ids, user)
        await _broadcast_room_membership(db, room_id, {"type": chat_realtime.EVENT_MEMBERSHIP_UPDATED, "room_id": room_id})
        return room

    @api_router.delete("/chat/rooms/{room_id}/members")
    async def delete_members(room_id: str, payload: MembersPayload, user=Depends(require_perm(PERM_CHAT_USE, PERM_CHAT_GROUP_MANAGE))):
        db = get_db()
        room = await chat_rooms.remove_members(db, room_id, payload.user_ids, user)
        removed = list(room.get("removed_user_ids") or [])
        await _broadcast_room_membership(
            db,
            room_id,
            {"type": chat_realtime.EVENT_MEMBERSHIP_UPDATED, "room_id": room_id, "removed_user_ids": removed},
            also_user_ids=removed,
        )
        for uid in removed:
            await chat_realtime.manager.unsubscribe_user_from_room(uid, room_id)
        return room

    @api_router.post("/chat/rooms/{room_id}/archive")
    async def post_archive(room_id: str, user=Depends(require_perm(PERM_CHAT_USE, PERM_CHAT_GROUP_MANAGE))):
        room = await chat_rooms.archive_room(get_db(), room_id, user)
        await chat_realtime.broadcast_to_room(room_id, {"type": chat_realtime.EVENT_ROOM_UPDATED, "room": room})
        return room

    @api_router.post("/chat/rooms/work", status_code=201)
    async def post_work_room(payload: WorkRoomPayload, user=Depends(require_perm(PERM_CHAT_USE))):
        db = get_db()
        await ensure_indexes(db)
        snap = await chat_work.resolve_work_snapshot(
            db,
            user,
            order_id=payload.order_id,
            line_id=payload.line_id,
            line_idx=payload.line_idx,
            task_id=payload.task_id,
        )
        room = await chat_rooms.create_or_get_work_room(db, user, snap, payload.member_ids)
        await _broadcast_room_membership(db, room["id"], {"type": chat_realtime.EVENT_MEMBERSHIP_UPDATED, "room_id": room["id"]})
        return room

    @api_router.get("/chat/rooms/{room_id}")
    async def get_room(room_id: str, user=Depends(require_perm(PERM_CHAT_USE))):
        return await chat_rooms.room_detail(get_db(), room_id, _uid(user))

    @api_router.get("/chat/rooms/{room_id}/messages")
    async def get_messages(room_id: str, before_sequence: Optional[int] = Query(None), limit: int = Query(40), user=Depends(require_perm(PERM_CHAT_USE))):
        return {"messages": await chat_messages.list_messages(get_db(), room_id, _uid(user), before_sequence=before_sequence, limit=limit)}

    @api_router.post("/chat/rooms/{room_id}/messages", status_code=201)
    async def post_message(room_id: str, payload: MessagePayload, user=Depends(require_perm(PERM_CHAT_USE))):
        db = get_db()
        msg = await chat_messages.send_message(db, room_id, user, **_dump(payload))
        await chat_realtime.broadcast_to_room(room_id, {"type": chat_realtime.EVENT_MESSAGE_CREATED, "message": msg})
        return msg

    @api_router.patch("/chat/messages/{message_id}")
    async def patch_message(message_id: str, payload: EditPayload, user=Depends(require_perm(PERM_CHAT_USE))):
        msg = await chat_messages.edit_message(get_db(), message_id, user, payload.content)
        await chat_realtime.broadcast_to_room(msg["room_id"], {"type": chat_realtime.EVENT_MESSAGE_EDITED, "message": msg})
        return msg

    @api_router.delete("/chat/messages/{message_id}")
    async def delete_message(message_id: str, user=Depends(require_perm(PERM_CHAT_USE))):
        can_moderate = await _has_perm(user_permissions, user, PERM_CHAT_MODERATE)
        msg = await chat_messages.delete_message(get_db(), message_id, user, can_moderate=can_moderate)
        await chat_realtime.broadcast_to_room(msg["room_id"], {"type": chat_realtime.EVENT_MESSAGE_DELETED, "message": msg})
        return msg

    @api_router.post("/chat/rooms/{room_id}/read")
    async def post_read(room_id: str, payload: ReadPayload, user=Depends(require_perm(PERM_CHAT_USE))):
        state = await chat_messages.mark_read(get_db(), room_id, _uid(user), payload.sequence)
        await chat_realtime.broadcast_to_room(room_id, {"type": chat_realtime.EVENT_READ_UPDATED, "read": state})
        return state

    @api_router.get("/chat/users")
    async def get_chat_users(q: Optional[str] = Query(None), user=Depends(require_perm(PERM_CHAT_USE))):
        text = str(q or "").strip()
        query = {"status": "active", "id": {"$ne": _uid(user)}}
        if text:
            query["$or"] = [{"name": {"$regex": text, "$options": "i"}}, {"email": {"$regex": text, "$options": "i"}}]
        users = await get_db().users.find(query, {"_id": 0, "password_hash": 0, "permissions_override": 0}).sort("name", 1).limit(50).to_list(50)
        return {"users": users}

    @api_router.get("/chat/work/orders")
    async def get_work_orders(q: Optional[str] = Query(None), user=Depends(require_perm(PERM_CHAT_USE))):
        return {"orders": await chat_work.search_work_orders(get_db(), user, q)}

    @api_router.get("/chat/work/orders/{order_id}/lines")
    async def get_work_lines(order_id: str, user=Depends(require_perm(PERM_CHAT_USE))):
        return {"lines": await chat_work.list_order_lines(get_db(), user, order_id)}

    @api_router.get("/chat/work/orders/{order_id}/tasks")
    async def get_work_tasks(order_id: str, line_idx: Optional[int] = Query(None), user=Depends(require_perm(PERM_CHAT_USE))):
        return {"tasks": await chat_work.list_order_tasks(get_db(), user, order_id, line_idx=line_idx)}

    @api_router.post("/chat/rooms/{room_id}/attachments", status_code=201)
    async def post_attachment(room_id: str, request: Request, user=Depends(require_perm(PERM_CHAT_USE))):
        db = get_db()
        await chat_rooms.require_active_member(db, room_id, _uid(user))
        ctype = request.headers.get("content-type", "").lower()
        if ctype.startswith("multipart/form-data"):
            form = await request.form()
            file = form.get("file")
            if not file or not hasattr(file, "read"):
                raise HTTPException(400, "File wajib diunggah")
            data = await file.read()
            meta = await chat_attachments.store_attachment_bytes(
                db,
                room_id=room_id,
                user=user,
                filename=getattr(file, "filename", None) or "lampiran",
                data=data,
                content_type=getattr(file, "content_type", "") or "",
            )
        else:
            body = DataUrlAttachmentPayload(**(await request.json()))
            meta = await chat_attachments.store_attachment_data_url(db, room_id=room_id, user=user, filename=body.filename, data_url=body.data_url)
        return meta

    @api_router.get("/chat/attachments/{attachment_id}/download")
    async def get_attachment(attachment_id: str, download: bool = Query(True), user=Depends(require_perm(PERM_CHAT_USE))):
        db = get_db()
        data, ct, filename, rec = await chat_attachments.load_attachment_bytes(db, attachment_id=attachment_id)
        await chat_rooms.require_active_member(db, rec.get("room_id"), _uid(user))
        return FastAPIResponse(
            content=data,
            media_type=ct,
            headers={"Content-Disposition": chat_attachments.content_disposition(filename, download=download), "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @api_router.websocket("/chat/ws")
    async def chat_ws(websocket: WebSocket):
        db = get_db()
        user = await authenticate_ws(websocket, db, user_permissions=user_permissions)
        await chat_realtime.manager.connect(websocket, _uid(user))
        try:
            while True:
                payload = await websocket.receive_json()
                op = payload.get("op")
                room_ids = [str(r) for r in payload.get("room_ids") or []]
                if op == "subscribe":
                    allowed = []
                    for rid in room_ids:
                        try:
                            await chat_rooms.require_active_member(db, rid, _uid(user))
                            allowed.append(rid)
                        except HTTPException:
                            continue
                    await chat_realtime.manager.subscribe(websocket, allowed)
                    await websocket.send_json({"type": "subscribed", "room_ids": allowed})
                elif op == "unsubscribe":
                    await chat_realtime.manager.unsubscribe(websocket, room_ids)
                    await websocket.send_json({"type": "unsubscribed", "room_ids": room_ids})
                else:
                    await websocket.send_json({"type": "error", "detail": "Operasi WebSocket tidak dikenal"})
        except WebSocketDisconnect:
            pass
        finally:
            await chat_realtime.manager.disconnect(websocket)
