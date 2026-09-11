"""HTTP routes for minimal in-app notifications + Customer Design Approval settings."""
from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from services import notifications as notif_svc


class NotificationSettingsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    notify_designer: bool
    notify_crm: bool


def _uid(user: dict) -> str:
    return notif_svc.user_canonical_id(user)


def register_notification_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends=Depends,
):
    @api_router.get("/notifications")
    async def list_my_notifications(
        limit: int = Query(50, ge=1, le=100),
        user=Depends(current_user),
    ):
        db = get_db()
        uid = _uid(user)
        if not uid:
            raise HTTPException(401, "Sesi tidak valid")
        items = await notif_svc.list_notifications_for_user(db, uid, limit=limit)
        return {"notifications": items}

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
        await require_perm(user, "customer_approval.view")
        db = get_db()
        settings = await notif_svc.get_notification_settings(db)
        return {"settings": settings}

    @api_router.put("/settings/notifications/customer-design-approval")
    async def put_customer_design_approval_notification_settings(
        payload: NotificationSettingsPayload,
        user=Depends(current_user),
    ):
        await require_perm(user, "customer_approval.settings")
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
