"""HTTP routes for Branch and Warehouse masters."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services.org_locations import (
    PERM_BRANCH_MANAGE,
    PERM_BRANCH_VIEW,
    PERM_WAREHOUSE_MANAGE,
    PERM_WAREHOUSE_VIEW,
    OrgLocationConflictError,
    OrgLocationValidationError,
    create_branch,
    create_warehouse,
    delete_branch,
    delete_warehouse,
    ensure_indexes,
    get_branch,
    get_warehouse,
    list_branches,
    list_warehouses,
    update_branch,
    update_warehouse,
)


class BranchCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    active: Optional[bool] = True


class BranchUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Optional[str] = None
    name: Optional[str] = None
    active: Optional[bool] = None


class WarehouseCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: str = Field(..., min_length=1)
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    active: Optional[bool] = True


class WarehouseUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch_id: Optional[str] = None
    code: Optional[str] = None
    name: Optional[str] = None
    active: Optional[bool] = None


def _dump(payload: BaseModel) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def register_org_locations_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/branches")
    async def get_branches(
        q: Optional[str] = Query(None),
        active: Optional[bool] = Query(None),
        user=Depends(require_perm(PERM_BRANCH_VIEW)),
    ):
        from services.branch_access import filter_branches_for_user

        db = get_db()
        items = await list_branches(db, q=q, active=active)
        items = await filter_branches_for_user(db, user, items)
        return {"items": items, "total": len(items)}

    @api_router.get("/branches/{branch_id}")
    async def get_branch_detail(
        branch_id: str,
        user=Depends(require_perm(PERM_BRANCH_VIEW)),
    ):
        from services.branch_access import require_branch_access

        db = get_db()
        await require_branch_access(db, user, branch_id)
        doc = await get_branch(db, branch_id)
        if not doc:
            raise HTTPException(404, "Cabang tidak ditemukan")
        return doc

    @api_router.post("/branches", status_code=201)
    async def post_branch(
        payload: BranchCreatePayload,
        user=Depends(require_perm(PERM_BRANCH_MANAGE)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            from services.operational_branch import require_branch_manager
            require_branch_manager(user)
            return await create_branch(db, _dump(payload), user=user)
        except OrgLocationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OrgLocationConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/branches/{branch_id}")
    async def patch_branch(
        branch_id: str,
        payload: BranchUpdatePayload,
        user=Depends(require_perm(PERM_BRANCH_MANAGE)),
    ):
        db = get_db()
        try:
            from services.operational_branch import require_branch_manager
            require_branch_manager(user)
            return await update_branch(db, branch_id, _dump(payload), user=user)
        except KeyError:
            raise HTTPException(404, "Cabang tidak ditemukan") from None
        except OrgLocationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OrgLocationConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/branches/{branch_id}")
    async def delete_branch_route(
        branch_id: str,
        user=Depends(require_perm(PERM_BRANCH_MANAGE)),
    ):
        db = get_db()
        try:
            from services.operational_branch import require_branch_manager
            require_branch_manager(user)
            return await delete_branch(db, branch_id, user=user)
        except KeyError:
            raise HTTPException(404, "Cabang tidak ditemukan") from None
        except OrgLocationConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.get("/warehouses")
    async def get_warehouses(
        branch_id: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        active: Optional[bool] = Query(None),
        user=Depends(require_perm(PERM_WAREHOUSE_VIEW)),
    ):
        from services.branch_access import filter_warehouses_for_user, require_branch_access

        db = get_db()
        if branch_id:
            await require_branch_access(db, user, branch_id)
        items = await list_warehouses(db, branch_id=branch_id, q=q, active=active)
        items = await filter_warehouses_for_user(db, user, items)
        return {"items": items, "total": len(items)}

    @api_router.get("/warehouses/{warehouse_id}")
    async def get_warehouse_detail(
        warehouse_id: str,
        user=Depends(require_perm(PERM_WAREHOUSE_VIEW)),
    ):
        from services.branch_access import require_warehouse_access

        db = get_db()
        await require_warehouse_access(db, user, warehouse_id)
        doc = await get_warehouse(db, warehouse_id)
        if not doc:
            raise HTTPException(404, "Gudang tidak ditemukan")
        return doc

    @api_router.post("/warehouses", status_code=201)
    async def post_warehouse(
        payload: WarehouseCreatePayload,
        user=Depends(require_perm(PERM_WAREHOUSE_MANAGE)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            return await create_warehouse(db, _dump(payload), user=user)
        except OrgLocationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OrgLocationConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/warehouses/{warehouse_id}")
    async def patch_warehouse(
        warehouse_id: str,
        payload: WarehouseUpdatePayload,
        user=Depends(require_perm(PERM_WAREHOUSE_MANAGE)),
    ):
        db = get_db()
        try:
            return await update_warehouse(db, warehouse_id, _dump(payload), user=user)
        except KeyError:
            raise HTTPException(404, "Gudang tidak ditemukan") from None
        except OrgLocationValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OrgLocationConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/warehouses/{warehouse_id}")
    async def delete_warehouse_route(
        warehouse_id: str,
        user=Depends(require_perm(PERM_WAREHOUSE_MANAGE)),
    ):
        db = get_db()
        try:
            return await delete_warehouse(db, warehouse_id, user=user)
        except KeyError:
            raise HTTPException(404, "Gudang tidak ditemukan") from None
        except OrgLocationConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
