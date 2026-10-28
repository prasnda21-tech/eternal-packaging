"""HTTP routes for minimal in-app notifications + notification settings."""
from __future__ import annotations

from typing import Dict, List, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services import notifications as notif_svc


class NotificationSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    notify_designer: bool
    notify_crm: bool
    additional_recipient_user_ids: Optional[List[str]] = None


class CrmProgressSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    events: Dict[str, bool] = Field(default_factory=dict)


def _uid(user: dict) -> str:
    return notif_svc.user_canonical_id(user)


def _can_view_notification_settings(user, require_perm) -> None:
    """Existing CDA perms OR new cross-module notification settings perms."""
    # require_perm raises; try CDA first for backward compat, then new keys.
    # Implemented via any-of below in register.


def register_notification_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends=Depends,
    require_any_perm=None,
):
    async def _require_settings_view(user):
        if require_any_perm is not None:
            await require_any_perm(
                user,
                "customer_approval.view",
                "customer_approval.settings",
                "notifications.settings.view",
                "notifications.settings.manage",
            )
            return
        try:
            await require_perm(user, "customer_approval.view")
        except HTTPException:
            await require_perm(user, "notifications.settings.view")

    async def _require_settings_manage(user):
        if require_any_perm is not None:
            await require_any_perm(
                user,
                "customer_approval.settings",
                "notifications.settings.manage",
            )
            return
        try:
            await require_perm(user, "customer_approval.settings")
        except HTTPException:
            await require_perm(user, "notifications.settings.manage")

    @api_router.get("/notifications")
    async def list_my_notifications(
        limit: int = Query(50, ge=1, le=100),
        module: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        before: Optional[str] = Query(None),
        user=Depends(current_user),
    ):
        db = get_db()
        uid = _uid(user)
        if not uid:
            raise HTTPException(401, "Sesi tidak valid")
        result = await notif_svc.list_notifications_for_user(
            db, uid, limit=limit, module=module, q=q, before=before,
        )
        return result

    @api_router.get("/notifications/unread-counts")
    async def my_notification_unread_counts(user=Depends(current_user)):
        db = get_db()
        uid = _uid(user)
        if not uid:
            raise HTTPException(401, "Sesi tidak valid")
        return await notif_svc.unread_counts_for_user(db, uid)

    @api_router.post("/notifications/{notification_id}/read")
    async def mark_my_notification_read(
        notification_id: str,
        user=Depends(current_user),
    ):
        db = get_db()
        uid = _uid(user)
        if not uid:
            raise HTTPException(401, "Sesi tidak valid")
        item = await notif_svc.mark_notification_read(db, uid, notification_id)
        if not item:
            raise HTTPException(404, "Notifikasi tidak ditemukan")
        return {"notification": item}

    @api_router.get("/settings/notifications/customer-design-approval")
    async def get_customer_design_approval_notification_settings(
        user=Depends(current_user),
    ):
        await _require_settings_view(user)
        db = get_db()
        settings = await notif_svc.get_notification_settings(db)
        return {"settings": settings}

    @api_router.put("/settings/notifications/customer-design-approval")
    async def put_customer_design_approval_notification_settings(
        payload: NotificationSettingsPayload,
        user=Depends(current_user),
    ):
        await _require_settings_manage(user)
        db = get_db()
        try:
            settings = await notif_svc.update_notification_settings(
                db,
                payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
                user=user,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"settings": settings, "message": "Pengaturan notifikasi disimpan"}

    @api_router.get("/settings/notifications/crm-progress")
    async def get_crm_progress_notification_settings(user=Depends(current_user)):
        await _require_settings_view(user)
        db = get_db()
        return {"settings": await notif_svc.get_crm_progress_settings(db)}

    @api_router.put("/settings/notifications/crm-progress")
    async def put_crm_progress_notification_settings(
        payload: CrmProgressSettingsPayload,
        user=Depends(current_user),
    ):
        await _require_settings_manage(user)
        db = get_db()
        try:
            settings = await notif_svc.update_crm_progress_settings(
                db,
                payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(),
                user=user,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"settings": settings, "message": "Pengaturan notifikasi progres CRM disimpan"}
