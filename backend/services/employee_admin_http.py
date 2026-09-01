"""HTTP routes for Administrasi Karyawan. Registered onto existing api_router."""
from __future__ import annotations

import uuid
from typing import Optional
from urllib.parse import quote

from fastapi import File, Form, HTTPException, UploadFile
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict

from services import storage as obj_storage
from services.employee_admin import (
    ALLOWED_FILE_TYPES,
    FILES_KIND,
    MAX_FILE_BYTES,
    PERM_APPROVE,
    PERM_DOCUMENT,
    PERM_SETTINGS,
    PERM_SUBMIT,
    PERM_VIEW_ALL,
    PERM_VIEW_OWN,
    REQUESTS_COLLECTION,
    can_view_document,
    can_view_request,
    create_type,
    decide_request,
    ensure_indexes,
    get_request,
    get_type,
    list_eligible_types,
    list_requests,
    list_types,
    now_iso,
    public_request,
    resolve_employee,
    sniff_content_type,
    submit_request,
    update_type,
    DIVISION_OPTIONS,
)


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_employee_admin_routes(api_router, *, get_db, current_user, require_perm, require_any_perm, user_permissions, Depends):
    def _dump(payload: LoosePayload) -> dict:
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        return payload.dict()

    async def _employee(user: dict):
        return await resolve_employee(get_db(), user)

    async def _perms(user: dict) -> set:
        return await user_permissions(user)

    @api_router.get("/employee-admin/meta")
    async def employee_admin_meta(user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS, PERM_SUBMIT, PERM_VIEW_OWN, PERM_APPROVE, PERM_VIEW_ALL, PERM_DOCUMENT)
        db = get_db()
        roles = await db.roles.find({}, {"_id": 0, "role": 1, "label": 1, "status": 1}).to_list(200)
        users = []
        if PERM_SETTINGS in await _perms(user) or user.get("role") == "admin":
            users = await db.users.find(
                {"status": {"$ne": "inactive"}},
                {"_id": 0, "id": 1, "name": 1, "email": 1, "role": 1, "employee_id": 1},
            ).sort("name", 1).to_list(500)
        return {
            "divisions": [{"id": i, "label": l} for i, l in DIVISION_OPTIONS],
            "roles": roles,
            "users": users,
            "field_types": ["text", "textarea", "number", "currency", "date", "datetime", "select", "checkbox", "file"],
        }

    @api_router.get("/employee-admin/types")
    async def list_request_types(include_inactive: bool = True, user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS)
        rows = await list_types(get_db(), include_inactive=include_inactive)
        return {"types": rows, "count": len(rows)}

    @api_router.get("/employee-admin/types/eligible")
    async def list_eligible_request_types(user=Depends(current_user)):
        await require_any_perm(user, PERM_SUBMIT, PERM_VIEW_OWN)
        employee = await _employee(user)
        rows = await list_eligible_types(get_db(), employee)
        return {"types": rows, "count": len(rows)}

    @api_router.get("/employee-admin/types/{type_id}")
    async def get_request_type(type_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS, PERM_SUBMIT)
        return {"type": await get_type(get_db(), type_id)}

    @api_router.post("/employee-admin/types")
    async def create_request_type(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS)
        doc = await create_type(get_db(), _dump(payload), user)
        return {"type": doc, "message": "Jenis pengajuan disimpan"}

    @api_router.put("/employee-admin/types/{type_id}")
    async def update_request_type(type_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS)
        doc = await update_type(get_db(), type_id, _dump(payload), user)
        return {"type": doc, "message": "Jenis pengajuan diperbarui"}

    @api_router.get("/employee-admin/requests")
    async def list_employee_requests(
        scope: str = "mine",
        q: Optional[str] = None,
        user=Depends(current_user),
    ):
        perms = await _perms(user)
        if scope == "approve":
            await require_any_perm(user, PERM_APPROVE)
        elif scope == "archive":
            await require_any_perm(user, PERM_DOCUMENT, PERM_VIEW_ALL, PERM_VIEW_OWN)
        elif scope == "all":
            await require_any_perm(user, PERM_VIEW_ALL, PERM_VIEW_OWN)
        else:
            await require_any_perm(user, PERM_VIEW_OWN, PERM_SUBMIT, PERM_VIEW_ALL)
        employee = await _employee(user)
        rows = await list_requests(get_db(), employee=employee, perms=perms, scope=scope, q=q or "")
        return {"requests": rows, "count": len(rows)}

    @api_router.get("/employee-admin/requests/{request_id}")
    async def get_employee_request(request_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_VIEW_OWN, PERM_SUBMIT, PERM_APPROVE, PERM_VIEW_ALL, PERM_DOCUMENT)
        employee = await _employee(user)
        perms = await _perms(user)
        doc = await get_request(get_db(), request_id)
        if not can_view_request(employee, doc, perms):
            raise HTTPException(403, "Anda tidak berhak melihat pengajuan ini.")
        return {"request": public_request(doc)}

    @api_router.post("/employee-admin/requests")
    async def create_employee_request(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_SUBMIT)
        employee = await _employee(user)
        doc = await submit_request(get_db(), _dump(payload), user, employee)
        return {"request": doc, "message": "Pengajuan berhasil dikirim"}

    @api_router.post("/employee-admin/requests/{request_id}/decide")
    async def decide_employee_request(request_id: str, payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_APPROVE)
        employee = await _employee(user)
        doc = await decide_request(get_db(), request_id, _dump(payload), user, employee)
        return {"request": doc, "message": "Keputusan persetujuan disimpan"}

    @api_router.post("/employee-admin/uploads")
    async def upload_employee_admin_file(
        file: UploadFile = File(...),
        field_key: str = Form(""),
        user=Depends(current_user),
    ):
        await require_any_perm(user, PERM_SUBMIT)
        raw = await file.read()
        if not raw:
            raise HTTPException(400, "File kosong.")
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(400, "Ukuran file maksimal 8 MB.")
        ct = sniff_content_type(raw, file.filename or "", file.content_type or "")
        if ct not in ALLOWED_FILE_TYPES:
            raise HTTPException(400, "Tipe file tidak diizinkan.")
        file_id = str(uuid.uuid4())
        ext = ALLOWED_FILE_TYPES[ct]
        storage_path = f"{obj_storage.APP_NAME}/employee-admin/uploads/{file_id}.{ext}"
        obj_storage.put_object(storage_path, raw, ct)
        await get_db().files.insert_one({
            "id": file_id,
            "kind": FILES_KIND,
            "storage_path": storage_path,
            "content_type": ct,
            "original_filename": file.filename or f"lampiran.{ext}",
            "field_key": field_key,
            "uploaded_by": str(user.get("email") or ""),
            "uploaded_at": now_iso(),
            "is_deleted": False,
        })
        return {
            "file": {
                "id": file_id,
                "name": file.filename or f"lampiran.{ext}",
                "content_type": ct,
                "field_key": field_key,
            }
        }

    def _file_headers(filename: str, download: bool):
        disposition = "attachment" if download else "inline"
        ascii_name = (filename or "dokumen").encode("ascii", "ignore").decode("ascii") or "dokumen"
        return {
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename or 'dokumen')}",
        }

    async def _load_accessible_request(request_id: str, user: dict, *, document=False):
        employee = await _employee(user)
        perms = await _perms(user)
        doc = await get_request(get_db(), request_id)
        ok = can_view_document(employee, doc, perms) if document else can_view_request(employee, doc, perms)
        if not ok:
            raise HTTPException(403, "Anda tidak berhak mengakses file ini.")
        return doc

    @api_router.get("/employee-admin/requests/{request_id}/files/{file_id}")
    async def download_request_file(request_id: str, file_id: str, download: bool = False, user=Depends(current_user)):
        await require_any_perm(user, PERM_VIEW_OWN, PERM_SUBMIT, PERM_APPROVE, PERM_VIEW_ALL, PERM_DOCUMENT)
        req = await _load_accessible_request(request_id, user, document=False)
        rec = await get_db().files.find_one({"id": file_id, "is_deleted": {"$ne": True}}, {"_id": 0})
        if not rec or rec.get("kind") != FILES_KIND:
            raise HTTPException(404, "File tidak ditemukan.")
        owned = rec.get("request_id") == request_id
        attached = any(str((a or {}).get("id")) == file_id for a in (req.get("attachments") or []))
        value_hit = any(
            isinstance(v, dict) and str(v.get("id")) == file_id
            for v in (req.get("values") or {}).values()
        )
        if rec.get("request_id") and not owned and not attached and not value_hit:
            raise HTTPException(403, "File tidak terkait pengajuan ini.")
        if not rec.get("storage_path"):
            raise HTTPException(404, "File tidak ditemukan.")
        try:
            data, ct = obj_storage.get_object(rec["storage_path"])
        except FileNotFoundError:
            raise HTTPException(404, "File tidak ditemukan.")
        media = rec.get("content_type") or ct
        return FastAPIResponse(
            content=data,
            media_type=media,
            headers=_file_headers(rec.get("original_filename") or "lampiran", download),
        )

    @api_router.get("/employee-admin/requests/{request_id}/document")
    async def download_request_document(request_id: str, download: bool = True, user=Depends(current_user)):
        await require_any_perm(user, PERM_DOCUMENT, PERM_VIEW_ALL, PERM_VIEW_OWN)
        req = await _load_accessible_request(request_id, user, document=True)
        docu = req.get("document") or {}
        path = docu.get("storage_path")
        if not path and docu.get("file_id"):
            rec = await get_db().files.find_one({"id": docu["file_id"]}, {"_id": 0})
            path = (rec or {}).get("storage_path")
        if not path:
            raise HTTPException(404, "Dokumen PDF belum tersedia.")
        try:
            data, ct = obj_storage.get_object(path)
        except FileNotFoundError:
            raise HTTPException(404, "Dokumen PDF tidak ditemukan.")
        name = f"{docu.get('document_number') or req.get('request_number') or 'dokumen'}.pdf"
        return FastAPIResponse(
            content=data,
            media_type="application/pdf",
            headers=_file_headers(name, download),
        )

    return ensure_indexes
