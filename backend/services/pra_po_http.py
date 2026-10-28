"""HTTP routes for Pra PO lifecycle."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Query
from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_pra_po_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    require_any_perm,
    Depends,
):
    from services import pra_po as svc

    async def _perms(user: dict) -> set:
        return await user_permissions(user)

    @api_router.get("/pra-po")
    async def list_pra_po_endpoint(
        view: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        page: Optional[int] = Query(None),
        page_size: Optional[int] = Query(None),
        user=Depends(current_user),
    ):
        """List Pra PO with optional search + pagination.

        Additive response fields (``total``, ``page``, ``page_size``, ``counts``)
        preserve the existing ``items`` key for compatibility.
        """
        perms = await _perms(user)
        return await svc.list_pra_pos(
            get_db(),
            user,
            perms,
            view=view,
            status=status,
            q=q,
            page=page,
            page_size=page_size,
        )

    @api_router.post("/pra-po")
    async def create_pra_po_endpoint(
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, svc.PERM_CREATE)
        await svc.ensure_indexes(get_db())
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"pra_po": await svc.create_draft(get_db(), data, user)}

    @api_router.get("/pra-po/{pra_po_id}")
    async def get_pra_po_endpoint(pra_po_id: str, user=Depends(current_user)):
        perms = await _perms(user)
        return {"pra_po": await svc.get_pra_po(get_db(), pra_po_id, user, perms)}

    @api_router.put("/pra-po/{pra_po_id}")
    async def update_pra_po_endpoint(
        pra_po_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        perms = await _perms(user)
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"pra_po": await svc.update_draft(get_db(), pra_po_id, data, user, perms)}

    @api_router.post("/pra-po/{pra_po_id}/submit")
    async def submit_pra_po_endpoint(pra_po_id: str, user=Depends(current_user)):
        perms = await _perms(user)
        return {"pra_po": await svc.submit_pra_po(get_db(), pra_po_id, user, perms)}

    @api_router.post("/pra-po/{pra_po_id}/return")
    async def return_pra_po_endpoint(
        pra_po_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        perms = await _perms(user)
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"pra_po": await svc.return_pra_po(get_db(), pra_po_id, data, user, perms)}

    @api_router.post("/pra-po/{pra_po_id}/verify")
    async def verify_pra_po_endpoint(pra_po_id: str, user=Depends(current_user)):
        perms = await _perms(user)
        return await svc.verify_pra_po(get_db(), pra_po_id, user, perms)

    @api_router.get("/pra-po/{pra_po_id}/history")
    async def pra_po_history_endpoint(pra_po_id: str, user=Depends(current_user)):
        perms = await _perms(user)
        return {"history": await svc.list_history(get_db(), pra_po_id, user, perms)}
