"""HTTP for Sales label candidates (one-time verification)."""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field


class ApproveNewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sales_name: Optional[str] = None
    branch_ids: Optional[List[str]] = None
    active: Optional[bool] = True


class LinkExistingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sales_master_id: str = Field(..., min_length=1)


def register_sales_label_candidates_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    Depends,
):
    from services import sales_label_candidates as svc
    from services import sales_master as sm
    from services import sales_master_aliases as aliases

    @api_router.get("/settings/sales-candidates")
    async def list_sales_candidates(
        status: Optional[str] = None,
        open_only: bool = True,
        user=Depends(current_user),
    ):
        await require_any_perm(user, sm.PERM_VIEW, sm.PERM_MANAGE, "permissions.manage")
        await svc.ensure_indexes(get_db())
        items = await svc.list_candidates(
            get_db(),
            status=status,
            open_only=bool(open_only) if status is None else False,
        )
        return {"items": items}

    @api_router.get("/settings/sales-master/{sales_id}/aliases")
    async def list_master_aliases(sales_id: str, user=Depends(current_user)):
        await require_any_perm(user, sm.PERM_VIEW, sm.PERM_MANAGE, "permissions.manage")
        await aliases.ensure_indexes(get_db())
        items = await aliases.list_aliases_for_master(get_db(), sales_id)
        return {"items": items}

    @api_router.post("/settings/sales-candidates/{candidate_id}/approve-new")
    async def approve_candidate_new(
        candidate_id: str,
        payload: ApproveNewPayload,
        user=Depends(current_user),
    ):
        await require_any_perm(user, sm.PERM_MANAGE, "permissions.manage")
        result = await svc.approve_as_new(
            get_db(),
            candidate_id=candidate_id,
            user=user,
            sales_name=payload.sales_name,
            branch_ids=payload.branch_ids,
            active=True if payload.active is None else bool(payload.active),
        )
        return {
            **result,
            "message": "Kandidat didaftarkan sebagai Master Sales baru",
            "mapping_unchanged": True,
        }

    @api_router.post("/settings/sales-candidates/{candidate_id}/link")
    async def link_candidate_existing(
        candidate_id: str,
        payload: LinkExistingPayload,
        user=Depends(current_user),
    ):
        await require_any_perm(user, sm.PERM_MANAGE, "permissions.manage")
        result = await svc.link_existing(
            get_db(),
            candidate_id=candidate_id,
            sales_master_id=payload.sales_master_id,
            user=user,
        )
        return {
            **result,
            "message": "Kandidat dihubungkan ke Master Sales",
            "mapping_unchanged": True,
        }
