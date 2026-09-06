"""HTTP routes for Sales Accounting Settings (Phase 0)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_sales_accounting_settings_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import sales_accounting_settings as sas

    def dump(payload) -> dict:
        if payload is None:
            return {}
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if hasattr(payload, "dict"):
            return payload.dict()
        return dict(payload or {})

    @api_router.get("/settings/sales-accounting")
    async def get_sales_accounting_settings_endpoint(user=Depends(current_user)):
        await require_any_perm(user, sas.PERM_VIEW, sas.PERM_MANAGE, "permissions.manage")
        return {"settings": await sas.get_sales_accounting_settings(get_db())}

    @api_router.put("/settings/sales-accounting")
    async def put_sales_accounting_settings_endpoint(
        payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, sas.PERM_MANAGE)
        settings = await sas.update_sales_accounting_settings(get_db(), dump(payload), user)
        return {"settings": settings, "message": "Pengaturan Akuntansi Penjualan disimpan"}
