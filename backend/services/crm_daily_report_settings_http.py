"""HTTP routes for CRM Daily Report dropdown settings."""
from __future__ import annotations

from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict


class CrmOptionsPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    no_order_reasons: Optional[List[dict]] = None
    complaint_categories: Optional[List[dict]] = None
    issue_categories: Optional[List[dict]] = None
    doc_version: Optional[int] = None


def register_crm_daily_report_settings_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import crm_daily_report_settings as svc

    @api_router.get("/settings/crm-daily-report-options")
    async def get_crm_daily_report_options(user=Depends(current_user)):
        await require_any_perm(user, svc.PERM_VIEW, svc.PERM_MANAGE, "permissions.manage")
        return {"settings": await svc.load_settings(get_db())}

    @api_router.put("/settings/crm-daily-report-options")
    async def put_crm_daily_report_options(payload: CrmOptionsPayload, user=Depends(current_user)):
        await require_any_perm(user, svc.PERM_MANAGE, "permissions.manage")
        data: dict[str, Any] = payload.model_dump(exclude_unset=True)
        settings = await svc.save_settings(get_db(), data, user)
        return {"settings": settings, "message": "Pengaturan dropdown CRM disimpan"}
