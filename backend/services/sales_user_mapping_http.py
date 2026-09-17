"""Admin-only HTTP for Accurate sales-label → users.id mapping."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict
from typing import Any, List, Optional


class MappingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: Optional[List[dict]] = None


def register_sales_user_mapping_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import sales_user_mapping as sumap

    @api_router.get("/settings/sales-user-mapping")
    async def get_sales_user_mapping_endpoint(user=Depends(current_user)):
        await require_any_perm(user, sumap.PERM_VIEW, sumap.PERM_MANAGE, "permissions.manage")
        doc = await sumap.load_mapping_doc(get_db())
        return {"settings": sumap.public_mapping(doc)}

    @api_router.put("/settings/sales-user-mapping")
    async def put_sales_user_mapping_endpoint(payload: MappingPayload, user=Depends(current_user)):
        await require_any_perm(user, sumap.PERM_MANAGE, "permissions.manage")
        settings = await sumap.save_mapping(get_db(), payload.model_dump(), user)
        return {"settings": settings, "message": "Mapping sales Accurate disimpan"}
