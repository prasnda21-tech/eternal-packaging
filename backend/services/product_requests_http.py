"""HTTP routes for CRM product requests."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_product_request_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    require_any_perm,
    Depends,
):
    from services.product_requests import (
        create_draft,
        ensure_indexes,
        get_form_context,
        get_request,
        list_requests,
        submit_draft,
        update_draft,
    )
    from services.sales_customers import (
        customer_is_active,
        list_sales_customers,
    )

    async def _perms(user: dict) -> set:
        return await user_permissions(user)

    @api_router.get("/product-requests/customers")
    async def search_product_request_customers(q: Optional[str] = None, user=Depends(current_user)):
        await require_any_perm(user, "product_request.create")
        customers = await list_sales_customers(get_db(), q=q)
        for row in customers:
            row["is_active"] = customer_is_active(row)
        return {"customers": customers}

    @api_router.get("/product-requests/context")
    async def product_request_context(user=Depends(current_user)):
        await require_any_perm(
            user,
            "product_request.create",
            "product_request.read_own_draft",
            "product_request.read_all",
        )
        return {"context": await get_form_context(get_db(), user)}

    @api_router.get("/product-requests")
    async def list_product_requests_endpoint(
        view: Optional[str] = Query(None),
        user=Depends(current_user),
    ):
        await require_any_perm(
            user,
            "product_request.create",
            "product_request.read_own_draft",
            "product_request.read_all",
        )
        perms = await _perms(user)
        return {"requests": await list_requests(get_db(), user, perms, view=view)}

    @api_router.post("/product-requests")
    async def create_product_request_endpoint(
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "product_request.create")
        await ensure_indexes(get_db())
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"request": await create_draft(get_db(), data, user)}

    @api_router.get("/product-requests/{request_id}")
    async def get_product_request_endpoint(request_id: str, user=Depends(current_user)):
        await require_any_perm(
            user,
            "product_request.create",
            "product_request.read_own_draft",
            "product_request.read_all",
        )
        perms = await _perms(user)
        return {"request": await get_request(get_db(), request_id, user, perms)}

    @api_router.patch("/product-requests/{request_id}")
    async def update_product_request_endpoint(
        request_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "product_request.create")
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"request": await update_draft(get_db(), request_id, data, user)}

    @api_router.post("/product-requests/{request_id}/submit")
    async def submit_product_request_endpoint(
        request_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, "product_request.create")
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"receipt": await submit_draft(get_db(), request_id, data, user)}
