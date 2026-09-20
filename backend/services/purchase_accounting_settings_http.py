"""HTTP routes for Purchase Accounting Settings (Phase 4A)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_purchase_accounting_settings_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import purchase_accounting_settings as pas

    def dump(payload) -> dict:
        if payload is None:
            return {}
        if hasattr(payload, "model_dump"):
            return payload.model_dump()
        if hasattr(payload, "dict"):
            return payload.dict()
        return dict(payload or {})

    @api_router.get("/settings/purchase-accounting")
    async def get_purchase_accounting_settings_endpoint(user=Depends(current_user)):
        await require_any_perm(user, pas.PERM_VIEW, pas.PERM_MANAGE, "permissions.manage")
        return {"settings": await pas.get_purchase_accounting_settings(get_db())}

    @api_router.put("/settings/purchase-accounting")
    async def put_purchase_accounting_settings_endpoint(
        payload: LoosePayload, user=Depends(current_user)
    ):
        await require_any_perm(user, pas.PERM_MANAGE)
        settings = await pas.update_purchase_accounting_settings(get_db(), dump(payload), user)
        return {"settings": settings, "message": "Pengaturan Akuntansi Pembelian disimpan"}
