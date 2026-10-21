"""HTTP routes for Sentralisasi Cat V1."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict

from services.paint_stock import (
    TX_ADD,
    TX_ADJUST,
    TX_OPENING,
    TX_RETURN,
    TX_TAKE,
    create_paint_master,
    ensure_indexes,
    export_usage_report_csv,
    http_error,
    list_eligible_employees,
    list_paint_branches,
    list_paint_master,
    list_stock_balances,
    list_transactions,
    post_transaction,
    summary_kpis,
    update_paint_master,
    usage_report,
)


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


PAINT_VIEW_ANY = (
    "paint.view",
    "paint.stock.view",
    "paint.report.view",
    "paint.settings.manage",
)


def register_paint_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    require_any_perm,
    Depends,
):
    async def _require(user, *perms: str):
        await require_any_perm(user, *perms)

    async def _require_all(user, *perms: str):
        eff = await user_permissions(user)
        missing = [p for p in perms if p not in eff]
        if missing:
            raise HTTPException(403, f"Permission '{missing[0]}' diperlukan")

    @api_router.get("/paint/master")
    async def paint_master_list(
        q: Optional[str] = None,
        active: Optional[bool] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        user=Depends(current_user),
    ):
        await _require(user, *PAINT_VIEW_ANY, "paint.settings.manage")
        try:
            return await list_paint_master(
                get_db(), q=q or "", active=active, page=page, page_size=page_size
            )
        except Exception as exc:
            raise http_error(exc)

    @api_router.post("/paint/master")
    async def paint_master_create(payload: LoosePayload, user=Depends(current_user)):
        await _require(user, "paint.settings.manage")
        await ensure_indexes(get_db())
        try:
            item = await create_paint_master(get_db(), payload.model_dump(), user)
            return {"item": item}
        except Exception as exc:
            raise http_error(exc)

    @api_router.patch("/paint/master/{paint_id}")
    async def paint_master_update(paint_id: str, payload: LoosePayload, user=Depends(current_user)):
        await _require(user, "paint.settings.manage")
        try:
            item = await update_paint_master(get_db(), paint_id, payload.model_dump(), user)
            return {"item": item}
        except Exception as exc:
            raise http_error(exc)

    @api_router.get("/paint/branches")
    async def paint_branches(user=Depends(current_user)):
        """Scoped branch list for paint UI filters/forms (does not require org.branches.view)."""
        await _require(user, *PAINT_VIEW_ANY)
        try:
            return await list_paint_branches(get_db(), user)
        except Exception as exc:
            raise http_error(exc)

    @api_router.get("/paint/employees/eligible")
    async def paint_eligible_employees(
        q: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=100),
        user=Depends(current_user),
    ):
        await _require(
            user,
            "paint.transaction.create",
            "paint.transaction.correct",
            "paint.view",
            "paint.report.view",
        )
        try:
            return await list_eligible_employees(
                get_db(), q=q or "", page=page, page_size=page_size
            )
        except Exception as exc:
            raise http_error(exc)

    @api_router.get("/paint/summary")
    async def paint_summary(
        branch_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        user=Depends(current_user),
    ):
        await _require(user, "paint.view", "paint.stock.view", "paint.report.view")
        try:
            return await summary_kpis(
                get_db(),
                user,
                branch_id=branch_id,
                date_from=date_from,
                date_to=date_to,
            )
        except Exception as exc:
            raise http_error(exc)

    @api_router.get("/paint/stock")
    async def paint_stock(
        branch_id: Optional[str] = None,
        q: Optional[str] = None,
        status: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        hf: Optional[list[str]] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        user=Depends(current_user),
    ):
        await _require(user, "paint.stock.view", "paint.view")
        try:
            return await list_stock_balances(
                get_db(),
                user,
                branch_id=branch_id,
                q=q or "",
                status=status,
                sort_by=sort_by,
                sort_dir=sort_dir,
                hf=hf,
                page=page,
                page_size=page_size,
            )
        except Exception as exc:
            raise http_error(exc)

    @api_router.get("/paint/transactions")
    async def paint_transactions(
        branch_id: Optional[str] = None,
        paint_id: Optional[str] = None,
        employee_user_id: Optional[str] = None,
        type: Optional[str] = Query(None, alias="type"),
        q: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        hf: Optional[list[str]] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        user=Depends(current_user),
    ):
        await _require(user, "paint.view", "paint.transaction.create", "paint.report.view")
        try:
            return await list_transactions(
                get_db(),
                user,
                branch_id=branch_id,
                paint_id=paint_id,
                employee_user_id=employee_user_id,
                tx_type=type,
                q=q or "",
                date_from=date_from,
                date_to=date_to,
                sort_by=sort_by,
                sort_dir=sort_dir,
                hf=hf,
                page=page,
                page_size=page_size,
            )
        except Exception as exc:
            raise http_error(exc)

    async def _post_typed(payload: dict, user, expected_type: str, *perms: str):
        await _require(user, *perms)
        await ensure_indexes(get_db())
        data = dict(payload or {})
        data["type"] = expected_type
        try:
            item = await post_transaction(get_db(), data, user)
            return {"item": item}
        except Exception as exc:
            raise http_error(exc)

    @api_router.post("/paint/transactions/opening")
    async def paint_opening(payload: LoosePayload, user=Depends(current_user)):
        return await _post_typed(payload.model_dump(), user, TX_OPENING, "paint.stock.opening")

    @api_router.post("/paint/transactions/add")
    async def paint_add(payload: LoosePayload, user=Depends(current_user)):
        return await _post_typed(payload.model_dump(), user, TX_ADD, "paint.stock.add")

    @api_router.post("/paint/transactions/take")
    async def paint_take(payload: LoosePayload, user=Depends(current_user)):
        return await _post_typed(payload.model_dump(), user, TX_TAKE, "paint.transaction.create")

    @api_router.post("/paint/transactions/return")
    async def paint_return(payload: LoosePayload, user=Depends(current_user)):
        return await _post_typed(payload.model_dump(), user, TX_RETURN, "paint.transaction.create")

    @api_router.post("/paint/transactions/adjust")
    async def paint_adjust(payload: LoosePayload, user=Depends(current_user)):
        return await _post_typed(payload.model_dump(), user, TX_ADJUST, "paint.transaction.correct")

    @api_router.get("/paint/reports/usage")
    async def paint_usage_report(
        branch_id: Optional[str] = None,
        employee_user_id: Optional[str] = None,
        paint_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        hf: Optional[list[str]] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        user=Depends(current_user),
    ):
        await _require(user, "paint.report.view")
        try:
            return await usage_report(
                get_db(),
                user,
                branch_id=branch_id,
                employee_user_id=employee_user_id,
                paint_id=paint_id,
                date_from=date_from,
                date_to=date_to,
                sort_by=sort_by,
                sort_dir=sort_dir,
                hf=hf,
                page=page,
                page_size=page_size,
            )
        except Exception as exc:
            raise http_error(exc)

    @api_router.get("/paint/reports/usage/export")
    async def paint_usage_export(
        branch_id: Optional[str] = None,
        employee_user_id: Optional[str] = None,
        paint_id: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        user=Depends(current_user),
    ):
        await _require(user, "paint.report.export")
        try:
            csv_text = await export_usage_report_csv(
                get_db(),
                user,
                branch_id=branch_id,
                employee_user_id=employee_user_id,
                paint_id=paint_id,
                date_from=date_from,
                date_to=date_to,
            )
            return PlainTextResponse(
                csv_text,
                media_type="text/csv; charset=utf-8",
                headers={"Content-Disposition": 'attachment; filename="laporan-penggunaan-cat.csv"'},
            )
        except Exception as exc:
            raise http_error(exc)
