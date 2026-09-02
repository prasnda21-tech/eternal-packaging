"""HTTP routes for Sales Activity / Outlet Ownership."""
from __future__ import annotations

from typing import Optional

from fastapi import File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict

from services.sales_activity import activities as activity_svc
from services.sales_activity import photos as photo_svc
from services.sales_activity import evaluator as evaluator_svc
from services.sales_activity import import_service
from services.sales_activity import ownership as ownership_svc
from services.sales_activity import outlets as outlet_svc
from services.sales_activity import pool as pool_svc
from services.sales_activity.constants import (
    PERM_ACTIVATE,
    PERM_ACTIVITY_CREATE,
    PERM_IMPORT,
    PERM_NAV,
    PERM_OUTLET_CREATE,
    PERM_OWNERSHIP_MANAGE,
    PERM_POOL_MANAGE,
    PERM_SETTINGS_MANAGE,
    PERM_SETTINGS_VIEW,
    PERM_VIEW,
)
from services.sales_activity.settings import load_settings, save_settings
from services.sales_activity.users import list_users
from services.sales_activity.util import actor_id, actor_name


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


class ReasonPayload(BaseModel):
    reason: str = ""
    owner_id: Optional[str] = None
    next_owner_id: Optional[str] = None
    extra_days: Optional[int] = None
    custom_expiry: Optional[str] = None
    exempt: Optional[bool] = None
    confirm: Optional[bool] = None


def _actor(user: dict) -> dict:
    return {
        "id": actor_id(user),
        "name": actor_name(user),
        "email": user.get("email"),
        "role": user.get("role"),
    }


def register_sales_activity_routes(api_router, *, get_db, current_user, require_perm, require_any_perm, user_permissions, Depends):
    async def _perms(user) -> set:
        return await user_permissions(user)

    @api_router.get("/sales-activity/settings")
    async def get_settings(user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS_VIEW, PERM_SETTINGS_MANAGE, PERM_OWNERSHIP_MANAGE, PERM_VIEW)
        return {"settings": await load_settings(get_db())}

    @api_router.put("/sales-activity/settings")
    async def put_settings(payload: LoosePayload, user=Depends(current_user)):
        await require_perm(user, PERM_SETTINGS_MANAGE)
        users = await list_users(get_db())
        known = {u["id"] for u in users}
        settings = await save_settings(get_db(), payload.model_dump(), _actor(user), known_user_ids=known)
        return {"settings": settings}

    @api_router.get("/sales-activity/sales-users")
    async def sales_users(user=Depends(current_user)):
        await require_any_perm(user, PERM_SETTINGS_MANAGE, PERM_OWNERSHIP_MANAGE, PERM_POOL_MANAGE)
        return {"users": await list_users(get_db())}

    @api_router.get("/sales-activity/outlets")
    async def list_outlets(
        q: Optional[str] = None,
        owner_id: Optional[str] = None,
        automation_status: Optional[str] = None,
        customer_kind: Optional[str] = None,
        in_pool: Optional[bool] = None,
        page: int = 1,
        page_size: int = 50,
        user=Depends(current_user),
    ):
        await require_any_perm(user, PERM_VIEW, PERM_NAV, PERM_OUTLET_CREATE, PERM_ACTIVITY_CREATE)
        return await outlet_svc.list_outlets(
            get_db(),
            user,
            await _perms(user),
            q=q or "",
            owner_id=owner_id or "",
            automation_status=automation_status or "",
            customer_kind=customer_kind or "",
            in_pool=in_pool,
            page=page,
            page_size=page_size,
        )

    @api_router.post("/sales-activity/outlets")
    async def create_outlet(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_OUTLET_CREATE, PERM_OWNERSHIP_MANAGE)
        return await outlet_svc.create_native_outlet(get_db(), payload.model_dump(), user, await _perms(user))

    @api_router.get("/sales-activity/outlets/{outlet_id}")
    async def outlet_detail(outlet_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_VIEW, PERM_NAV, PERM_ACTIVITY_CREATE)
        return await outlet_svc.get_outlet_detail(get_db(), outlet_id, user, await _perms(user))

    @api_router.get("/sales-activity/activities")
    async def list_activities(outlet_id: Optional[str] = None, page: int = 1, page_size: int = 50, user=Depends(current_user)):
        await require_any_perm(user, PERM_VIEW, PERM_ACTIVITY_CREATE)
        return await activity_svc.list_activities(get_db(), user, await _perms(user), outlet_id=outlet_id or "", page=page, page_size=page_size)

    @api_router.post("/sales-activity/activities")
    async def create_activity(payload: LoosePayload, user=Depends(current_user)):
        await require_any_perm(user, PERM_ACTIVITY_CREATE, PERM_OWNERSHIP_MANAGE)
        return await activity_svc.create_native_activity(get_db(), payload.model_dump(), user, await _perms(user))

    @api_router.get("/sales-activity/activities/{activity_id}")
    async def activity_detail(activity_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_VIEW, PERM_ACTIVITY_CREATE, PERM_OWNERSHIP_MANAGE)
        return await activity_svc.get_activity(get_db(), activity_id, user, await _perms(user))

    @api_router.post("/sales-activity/visit-photos")
    async def upload_visit_photo(
        file: UploadFile = File(...),
        outlet_id: str = Form(...),
        activity_id: str = Form(""),
        latitude: str = Form(""),
        longitude: str = Form(""),
        accuracy: str = Form(""),
        location_status: str = Form(""),
        client_captured_at: str = Form(""),
        uploaded_by: str = Form(""),
        sales_id: str = Form(""),
        owner_id: str = Form(""),
        user=Depends(current_user),
    ):
        await require_any_perm(user, PERM_ACTIVITY_CREATE, PERM_OWNERSHIP_MANAGE)
        content = await file.read()
        return await photo_svc.upload_visit_photo(
            get_db(),
            user=user,
            perms=await _perms(user),
            outlet_id=outlet_id,
            filename=file.filename or "visit.jpg",
            content_type=file.content_type or "",
            data=content,
            latitude=latitude,
            longitude=longitude,
            accuracy=accuracy,
            location_status_hint=location_status,
            client_captured_at=client_captured_at,
            activity_id=activity_id,
            uploaded_by=uploaded_by,
            sales_id=sales_id,
            owner_id=owner_id,
        )

    @api_router.get("/sales-activity/visit-photos/{photo_id}/file")
    async def download_visit_photo(photo_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_VIEW, PERM_ACTIVITY_CREATE, PERM_OWNERSHIP_MANAGE, PERM_NAV)
        data, media, filename = await photo_svc.download_photo(get_db(), photo_id, user, await _perms(user))
        return Response(
            content=data,
            media_type=media,
            headers={
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": "private, no-store",
                "Content-Disposition": f'inline; filename="{filename}"',
            },
        )

    @api_router.delete("/sales-activity/visit-photos/{photo_id}")
    async def delete_visit_photo(photo_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_ACTIVITY_CREATE, PERM_OWNERSHIP_MANAGE)
        return await photo_svc.delete_photo(get_db(), photo_id, user, await _perms(user))

    @api_router.get("/sales-activity/pool")
    async def list_pool(q: Optional[str] = None, page: int = 1, page_size: int = 50, user=Depends(current_user)):
        await require_any_perm(user, PERM_POOL_MANAGE, PERM_OWNERSHIP_MANAGE)
        return await pool_svc.list_pool(get_db(), q=q or "", page=page, page_size=page_size)

    @api_router.post("/sales-activity/outlets/{outlet_id}/reassign")
    async def reassign(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.manual_reassign(get_db(), outlet_id, payload.owner_id or "", payload.reason, _actor(user), await _perms(user))

    @api_router.post("/sales-activity/outlets/{outlet_id}/extend")
    async def extend(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.extend_or_custom_expiry(
            get_db(), outlet_id, reason=payload.reason, actor=_actor(user), perms=await _perms(user),
            extra_days=payload.extra_days, custom_expiry=payload.custom_expiry,
        )

    @api_router.post("/sales-activity/outlets/{outlet_id}/pause")
    async def pause(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.pause_automation(get_db(), outlet_id, payload.reason, _actor(user), await _perms(user))

    @api_router.post("/sales-activity/outlets/{outlet_id}/resume")
    async def resume(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.resume_automation(get_db(), outlet_id, payload.reason, _actor(user), await _perms(user))

    @api_router.post("/sales-activity/outlets/{outlet_id}/send-to-pool")
    async def send_pool(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.send_to_pool_manual(get_db(), outlet_id, payload.reason, _actor(user), await _perms(user))

    @api_router.post("/sales-activity/outlets/{outlet_id}/assign-from-pool")
    async def assign_pool(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.assign_from_pool(get_db(), outlet_id, payload.owner_id or "", payload.reason, _actor(user), await _perms(user))

    @api_router.post("/sales-activity/outlets/{outlet_id}/override-next")
    async def override_next(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.override_next_sales(get_db(), outlet_id, payload.next_owner_id or payload.owner_id or "", payload.reason, _actor(user), await _perms(user))

    @api_router.post("/sales-activity/outlets/{outlet_id}/exempt")
    async def exempt(outlet_id: str, payload: ReasonPayload, user=Depends(current_user)):
        return await ownership_svc.set_exempt(get_db(), outlet_id, bool(payload.exempt), payload.reason, _actor(user), await _perms(user))

    @api_router.post("/sales-activity/import/preview")
    async def import_preview(file: UploadFile = File(...), user=Depends(current_user)):
        await require_perm(user, PERM_IMPORT)
        content = await file.read()
        return await import_service.preview_import(get_db(), file.filename or "upload.xlsx", content, user)

    @api_router.get("/sales-activity/import/batches")
    async def import_batches(user=Depends(current_user)):
        await require_any_perm(user, PERM_IMPORT, PERM_ACTIVATE, PERM_OWNERSHIP_MANAGE)
        return {"items": await import_service.list_batches(get_db())}

    @api_router.get("/sales-activity/import/batches/{batch_id}")
    async def import_batch(batch_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_IMPORT, PERM_ACTIVATE, PERM_OWNERSHIP_MANAGE)
        return {"batch": await import_service.get_batch(get_db(), batch_id)}

    @api_router.post("/sales-activity/import/batches/{batch_id}/confirm")
    async def import_confirm(batch_id: str, user=Depends(current_user)):
        await require_perm(user, PERM_IMPORT)
        return await import_service.confirm_import(get_db(), batch_id, user)

    @api_router.get("/sales-activity/import/batches/{batch_id}/activation-preview")
    async def import_activation_preview(batch_id: str, user=Depends(current_user)):
        await require_any_perm(user, PERM_ACTIVATE, PERM_OWNERSHIP_MANAGE)
        return await import_service.activation_preview(get_db(), batch_id)

    @api_router.post("/sales-activity/import/batches/{batch_id}/activate")
    async def import_activate(batch_id: str, payload: ReasonPayload, user=Depends(current_user)):
        await require_perm(user, PERM_ACTIVATE)
        return await import_service.activate_migration(get_db(), batch_id, user, confirm=bool(payload.confirm))

    @api_router.post("/sales-activity/evaluate")
    async def evaluate(user=Depends(current_user)):
        await require_perm(user, PERM_OWNERSHIP_MANAGE)
        return await evaluator_svc.run_evaluator(get_db())
