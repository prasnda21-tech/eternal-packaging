"""HTTP API for Pengaturan → Desain Cetakan."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from services.print_template_model import (
    DATE_FORMATS,
    DOCUMENT_TYPES,
    FONTS,
    NUMBER_FORMATS,
    PAPER_PRESETS,
    field_catalog,
    validate_design,
)
from services import print_templates as svc


class TemplateCreate(BaseModel):
    name: str
    document_type: str
    audience: str = "customer"


class TemplateDesignBody(BaseModel):
    design: dict
    expected_revision: int


class TemplateRevisionBody(BaseModel):
    expected_revision: int


class TemplateCopyBody(BaseModel):
    name: str = ""


class TemplatePreviewBody(BaseModel):
    design: Optional[dict] = None


class TemplateValidateBody(BaseModel):
    document_type: str
    design: dict


def register(api_router: APIRouter, *, db, current_user, user_permissions, obj_storage):
    async def _perms(user):
        if user.get("role") == "admin" or user.get("is_super_admin"):
            return {"print_template.view", "print_template.manage"}
        return await user_permissions(user)

    async def can_view(user=Depends(current_user)):
        perms = await _perms(user)
        if "print_template.view" not in perms and "print_template.manage" not in perms:
            raise HTTPException(403, "Permission print_template.view diperlukan")
        return user

    async def can_manage(user=Depends(current_user)):
        perms = await _perms(user)
        if "print_template.manage" not in perms:
            raise HTTPException(403, "Permission print_template.manage diperlukan")
        return user

    @api_router.get("/print-templates/catalog")
    async def print_template_catalog(document_type: str, user=Depends(can_view)):
        if document_type not in DOCUMENT_TYPES:
            raise HTTPException(400, "Jenis dokumen tidak didukung.")
        return {
            "document_type": document_type,
            "fields": field_catalog(document_type),
            "fonts": list(FONTS.keys()),
            "date_formats": list(DATE_FORMATS),
            "number_formats": list(NUMBER_FORMATS),
            "paper": [{"id": key, "width_mm": val[0], "height_mm": val[1], "label": f"{key} ({int(val[0])} × {int(val[1])} mm)"} for key, val in PAPER_PRESETS.items()],
            "bands": [
                {"id": "title", "label": "Title"},
                {"id": "page_header", "label": "Page Header"},
                {"id": "detail", "label": "Detail"},
                {"id": "summary", "label": "Summary"},
                {"id": "page_footer", "label": "Page Footer"},
            ],
        }

    @api_router.post("/print-templates/validate")
    async def print_template_validate(payload: TemplateValidateBody, user=Depends(can_view)):
        errors = validate_design(payload.design, payload.document_type)
        return {"ok": not errors, "errors": errors}

    @api_router.get("/print-templates/active")
    async def print_template_active(document_type: str, user=Depends(current_user)):
        perms = await _perms(user)
        allowed = svc.user_can(user, perms, "print_template.view", "print_template.manage") or svc.can_print_document(user, perms, document_type)
        if not allowed:
            raise HTTPException(403, "Anda tidak dapat melihat pilihan cetakan.")
        return {"templates": await svc.active_print_options(db, document_type)}

    @api_router.get("/print-templates")
    async def print_template_list(document_type: str = "", status: str = "", user=Depends(can_view)):
        return {"templates": await svc.list_templates(db, document_type=document_type, status=status)}

    @api_router.post("/print-templates")
    async def print_template_create(payload: TemplateCreate, user=Depends(can_manage)):
        return {"template": await svc.create_template(db, payload.model_dump(), user)}

    @api_router.get("/print-templates/{template_id}")
    async def print_template_detail(template_id: str, user=Depends(can_view)):
        return {"template": await svc.get_template(db, template_id)}

    @api_router.put("/print-templates/{template_id}/draft")
    async def print_template_save_draft(template_id: str, payload: TemplateDesignBody, user=Depends(can_manage)):
        return {"template": await svc.save_draft(db, template_id, payload.design, payload.expected_revision, user)}

    @api_router.post("/print-templates/{template_id}/activate")
    async def print_template_activate(template_id: str, payload: TemplateRevisionBody, user=Depends(can_manage)):
        return {"template": await svc.activate_template(db, template_id, payload.expected_revision, user)}

    @api_router.post("/print-templates/{template_id}/copy")
    async def print_template_copy(template_id: str, payload: TemplateCopyBody, user=Depends(can_manage)):
        return {"template": await svc.copy_template(db, template_id, user, payload.name)}

    @api_router.post("/print-templates/{template_id}/default")
    async def print_template_default(template_id: str, payload: TemplateRevisionBody, user=Depends(can_manage)):
        return {"template": await svc.set_default(db, template_id, payload.expected_revision, user)}

    @api_router.post("/print-templates/{template_id}/archive")
    async def print_template_archive(template_id: str, payload: TemplateRevisionBody, user=Depends(can_manage)):
        return {"template": await svc.archive_template(db, template_id, payload.expected_revision, user)}

    @api_router.get("/print-templates/{template_id}/versions")
    async def print_template_versions(template_id: str, user=Depends(can_view)):
        return {"versions": await svc.list_versions(db, template_id)}

    @api_router.post("/print-templates/{template_id}/versions/{version_id}/restore")
    async def print_template_restore(template_id: str, version_id: str, payload: TemplateRevisionBody, user=Depends(can_manage)):
        return {"template": await svc.restore_version(db, template_id, version_id, payload.expected_revision, user)}

    @api_router.post("/print-templates/{template_id}/preview")
    async def print_template_preview(template_id: str, payload: TemplatePreviewBody, user=Depends(can_view)):
        pdf = await svc.preview_template(db, template_id, payload.design)
        return Response(content=pdf, media_type="application/pdf", headers={
            "Content-Disposition": 'inline; filename="pratinjau-desain.pdf"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        })

    @api_router.post("/print-templates/assets")
    async def print_template_asset_upload(file: UploadFile = File(...), user=Depends(can_manage)):
        data = await file.read()
        content_type = str(file.content_type or "")
        if content_type not in ("image/png", "image/jpeg"):
            name = str(file.filename or "").lower()
            if name.endswith(".png"):
                content_type = "image/png"
            elif name.endswith(".jpg") or name.endswith(".jpeg"):
                content_type = "image/jpeg"
        asset = await svc.store_asset(db, obj_storage, data, content_type, user)
        return {"asset": asset}

    @api_router.get("/print-templates/assets/{asset_id}")
    async def print_template_asset(asset_id: str, user=Depends(can_view)):
        data, content_type = await svc.load_asset(db, obj_storage, asset_id)
        return Response(content=data, media_type=content_type, headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
