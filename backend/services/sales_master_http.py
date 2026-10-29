"""HTTP routes for Master Sales settings + operator catalog."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SalesMasterPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sales_name: Optional[str] = None
    user_id: Optional[str] = None
    branch_ids: Optional[list] = None
    active: Optional[bool] = None
    sort_order: Optional[int] = None
    doc_version: Optional[int] = None


def register_sales_master_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import sales_master as svc

    @api_router.get("/settings/sales-master")
    async def list_settings_sales_master(
        q: Optional[str] = None,
        active: Optional[bool] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, svc.PERM_VIEW, svc.PERM_MANAGE, "permissions.manage")
        await svc.ensure_indexes(get_db())
        rows = await svc.list_sales_master(
            get_db(),
            active_only=bool(active) if active is not None else False,
            q=q or "",
            for_catalog=False,
        )
        if active is False:
            rows = [r for r in rows if not r.get("active")]
        return {"items": rows}

    @api_router.post("/settings/sales-master")
    async def create_settings_sales_master(payload: SalesMasterPayload, user=Depends(current_user)):
        await require_any_perm(user, svc.PERM_MANAGE, "permissions.manage")
        await svc.ensure_indexes(get_db())
        item = await svc.create_sales_master(get_db(), payload.model_dump(), user)
        return {"item": item, "message": "Master Sales ditambahkan"}

    @api_router.patch("/settings/sales-master/{sales_id}")
    async def patch_settings_sales_master(
        sales_id: str,
        payload: SalesMasterPayload,
        user=Depends(current_user),
    ):
        await require_any_perm(user, svc.PERM_MANAGE, "permissions.manage")
        data = payload.model_dump(exclude_unset=True)
        item = await svc.update_sales_master(get_db(), sales_id, data, user)
        return {"item": item, "message": "Master Sales diperbarui"}

    @api_router.get("/sales-master/catalog")
    async def sales_master_catalog(
        q: Optional[str] = None,
        user=Depends(current_user),
    ):
        """Active Master Sales for PO/CRM operators. Uses business module perms, not settings.manage."""
        await require_any_perm(
            user,
            "sales_order.create",
            "po.edit",
            "po.view",
            "crm.daily_report.create",
            "crm.daily_report.edit_own",
            "crm.daily_report.view_own",
            "crm.daily_report.view_all",
            "pra_po.create",
            "pra_po.edit_own",
            "pra_po.view_own",
            "pra_po.view_all",
            "pra_po.verify",
            svc.PERM_VIEW,
            svc.PERM_MANAGE,
        )
        await svc.ensure_indexes(get_db())
        rows = await svc.list_sales_master(
            get_db(),
            user=user,
            active_only=True,
            q=q or "",
            for_catalog=True,
        )
        return {"items": rows}
