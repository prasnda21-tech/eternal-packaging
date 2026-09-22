"""HTTP routes for SPK Antar Divisi."""
from __future__ import annotations

import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from services import storage as obj_storage
from services.employee_admin import ALLOWED_FILE_TYPES, MAX_FILE_BYTES, sniff_content_type
from services.spk import FILES_KIND, now_iso


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def _dump(payload: Optional[LoosePayload]) -> dict:
    return payload.model_dump() if payload else {}


def register_spk_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    require_any_perm,
    Depends,
):
    from services.spk import (
        PERM_ASSIGN,
        PERM_CANCEL,
        PERM_CREATE,
        PERM_MANAGE,
        PERM_PROGRESS,
        PERM_VERIFY,
        PERM_VIEW_ALL,
        PERM_VIEW_DIVISION,
        PERM_VIEW_OWN,
        acknowledge_spk,
        cancel_spk,
        create_spk,
        decide_verification,
        delete_draft,
        ensure_indexes,
        form_context,
        get_order,
        list_events,
        list_orders,
        publish_spk,
        record_progress,
        request_verification,
        start_spk,
        update_draft,
    )

    view_perms = (
        PERM_VIEW_OWN,
        PERM_VIEW_DIVISION,
        PERM_VIEW_ALL,
        PERM_CREATE,
        PERM_MANAGE,
        PERM_PROGRESS,
        PERM_VERIFY,
        PERM_ASSIGN,
    )

    async def _perms(user: dict) -> set:
        if user.get("role") == "admin":
            from permissions import ALL_PERMISSIONS
            return set(ALL_PERMISSIONS)
        return await user_permissions(user)

    @api_router.get("/spk/context")
    async def spk_context(user=Depends(current_user)):
        await require_any_perm(user, PERM_CREATE, PERM_ASSIGN, PERM_MANAGE)
        return {"context": await form_context(get_db(), user)}

    @api_router.get("/spk")
    async def spk_list(
        tab: Optional[str] = None,
        q: Optional[str] = None,
        status: Optional[str] = None,
        division: Optional[str] = None,
        pic: Optional[str] = None,
        priority: Optional[str] = None,
        deadline_state: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, *view_perms)
        perms = await _perms(user)
        return await list_orders(
            get_db(),
            user,
            perms,
            tab=tab or "mine",
            q=q or "",
            status=status or "",
            division=division or "",
            pic=pic or "",
            priority=priority or "",
            deadline=deadline_state or "",
        )

    @api_router.post("/spk")
    async def spk_create(payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_CREATE, PERM_MANAGE)
        await ensure_indexes(get_db())
        return {"order": await create_spk(get_db(), _dump(payload), user)}

    @api_router.get("/spk/{spk_id}")
    async def spk_detail(spk_id: str, user=Depends(current_user)):
        await require_any_perm(user, *view_perms)
        perms = await _perms(user)
        return {"order": await get_order(get_db(), spk_id, user, perms)}

    @api_router.patch("/spk/{spk_id}")
    async def spk_update(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_CREATE, PERM_MANAGE)
        perms = await _perms(user)
        return {"order": await update_draft(get_db(), spk_id, _dump(payload), user, perms)}

    @api_router.delete("/spk/{spk_id}")
    async def spk_delete(spk_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_CREATE, PERM_MANAGE)
        perms = await _perms(user)
        return await delete_draft(get_db(), spk_id, user, perms)

    @api_router.post("/spk/{spk_id}/publish")
    async def spk_publish(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_CREATE, PERM_ASSIGN, PERM_MANAGE)
        perms = await _perms(user)
        return {"order": await publish_spk(get_db(), spk_id, _dump(payload), user, perms)}

    @api_router.post("/spk/{spk_id}/acknowledge")
    async def spk_acknowledge(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, *view_perms)
        perms = await _perms(user)
        return {"order": await acknowledge_spk(get_db(), spk_id, _dump(payload), user, perms)}

    @api_router.post("/spk/{spk_id}/start")
    async def spk_start(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, *view_perms)
        perms = await _perms(user)
        return {"order": await start_spk(get_db(), spk_id, _dump(payload), user, perms)}

    @api_router.post("/spk/{spk_id}/progress")
    async def spk_progress(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_PROGRESS, PERM_MANAGE)
        perms = await _perms(user)
        return await record_progress(get_db(), spk_id, _dump(payload), user, perms)

    @api_router.post("/spk/{spk_id}/verification-request")
    async def spk_verification_request(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_PROGRESS, PERM_MANAGE)
        perms = await _perms(user)
        return {"order": await request_verification(get_db(), spk_id, _dump(payload), user, perms)}

    @api_router.post("/spk/{spk_id}/verify")
    async def spk_verify(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_VERIFY, PERM_MANAGE)
        perms = await _perms(user)
        return {"order": await decide_verification(get_db(), spk_id, _dump(payload), user, perms)}

    @api_router.post("/spk/{spk_id}/cancel")
    async def spk_cancel(spk_id: str, payload: Optional[LoosePayload] = None, user=Depends(current_user)):
        await require_any_perm(user, PERM_CANCEL, PERM_MANAGE)
        perms = await _perms(user)
        return {"order": await cancel_spk(get_db(), spk_id, _dump(payload), user, perms)}

    @api_router.get("/spk/{spk_id}/events")
    async def spk_events(spk_id: str, user=Depends(current_user)):
        await require_any_perm(user, *view_perms)
        perms = await _perms(user)
        return {"events": await list_events(get_db(), spk_id, user, perms)}

    @api_router.post("/spk/uploads")
    async def spk_upload(
        file: UploadFile = File(...),
        user=Depends(current_user),
    ):
        await require_any_perm(user, PERM_PROGRESS, PERM_CREATE, PERM_MANAGE)
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "File kosong.")
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(400, "Ukuran file maksimal 8 MB.")
        content_type = sniff_content_type(raw, file.filename or "", file.content_type or "")
        if content_type not in ALLOWED_FILE_TYPES:
            raise HTTPException(400, "Tipe file tidak diizinkan.")
        file_id = str(uuid.uuid4())
        ext = ALLOWED_FILE_TYPES[content_type]
        storage_path = f"{obj_storage.APP_NAME}/spk/{file_id}.{ext}"
        obj_storage.put_object(storage_path, raw, content_type)
        actor_id = str(user.get("user_id") or user.get("id") or user.get("sub") or "")
        await get_db().files.insert_one({
            "id": file_id,
            "kind": FILES_KIND,
            "storage_path": storage_path,
            "content_type": content_type,
            "original_filename": file.filename or f"lampiran.{ext}",
            "uploaded_by_id": actor_id,
            "uploaded_by": str(user.get("email") or ""),
            "uploaded_at": now_iso(),
            "is_deleted": False,
            "spk_id": "",
        })
        return {
            "file": {
                "id": file_id,
                "name": file.filename or f"lampiran.{ext}",
                "content_type": content_type,
            }
        }

    @api_router.get("/spk/{spk_id}/files/{file_id}")
    async def spk_file(spk_id: str, file_id: str, user=Depends(current_user)):
        await require_any_perm(user, *view_perms)
        perms = await _perms(user)
        order = await get_order(get_db(), spk_id, user, perms)
        known = {str(item.get("id")) for item in (order.get("attachments") or []) if item.get("kind") == "file"}
        if file_id not in known:
            raise HTTPException(404, "Lampiran tidak ditemukan.")
        rec = await get_db().files.find_one({"id": file_id, "kind": FILES_KIND, "is_deleted": {"$ne": True}}, {"_id": 0})
        if not rec or str(rec.get("spk_id") or "") != spk_id:
            raise HTTPException(404, "Lampiran tidak ditemukan.")
        data, content_type = obj_storage.get_object(rec["storage_path"])
        filename = rec.get("original_filename") or "lampiran"
        ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "lampiran"
        return Response(
            content=data,
            media_type=content_type or rec.get("content_type") or "application/octet-stream",
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Disposition": f"inline; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}",
            },
        )
