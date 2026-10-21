"""HTTP for Kartu Stok / Mutasi Persediaan (R2 ledger read-only)."""
from __future__ import annotations

from fastapi import HTTPException, Response
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from services.stock_reports import (
    PERM_EXPORT,
    PERM_ITEM,
    PERM_VIEW,
    StockReportError,
    catalog,
    delete_memorized,
    export_workbook,
    list_memorized,
    run_report,
    save_memorized,
    _filter_options,
)
from services.sales_user_mapping import reject_sales_pwa_bypass


class ReportRunBody(BaseModel):
    report_id: str = "kartu-stok"
    date_from: str
    date_to: str
    master_item_id: str = ""
    branch_id: str = ""
    warehouse_id: str = ""
    movement_type: str = ""
    reference_q: str = ""
    layout: Dict[str, Any] = Field(default_factory=dict)
    page: int = 1
    page_size: int = 25
    include_all: bool = False


class MemorizeBody(BaseModel):
    report_id: str = "kartu-stok"
    name: str
    description: str = ""
    layout: Dict[str, Any] = Field(default_factory=dict)


def _raise(exc: StockReportError) -> None:
    raise HTTPException(exc.status, exc.message) from exc


async def _require_view(user, user_permissions) -> None:
    perms = set(await user_permissions(user) or [])
    if PERM_VIEW not in perms:
        raise HTTPException(403, "Izin current_stock.view diperlukan.")
    if PERM_ITEM not in perms and "permissions.manage" not in perms:
        # Honor R2 item read gate — no bypass of master_items.view.
        raise HTTPException(403, "Izin master_items.view diperlukan untuk Kartu Stok.")


def register_stock_report_routes(
    api_router,
    *,
    get_db,
    require_perm,
    current_user,
    user_permissions,
    Depends,
):
    @api_router.get("/laporan/persediaan/katalog")
    async def stock_report_catalog(user=Depends(current_user)):
        reject_sales_pwa_bypass(user)
        await _require_view(user, user_permissions)
        db = get_db()
        try:
            options = await _filter_options(db, user)
            saved = await list_memorized(db, user)
        except StockReportError as exc:
            _raise(exc)
        return {
            "group": "Persediaan",
            "reports": catalog(),
            "branches": options["branches"],
            "warehouses": options["warehouses"],
            "can_switch_branches": options["can_switch_branches"],
            "branch_scope": options["branch_scope"],
            "memorized": saved,
        }

    @api_router.post("/laporan/persediaan/tampilkan")
    async def stock_report_show(body: ReportRunBody, user=Depends(current_user)):
        reject_sales_pwa_bypass(user)
        await _require_view(user, user_permissions)
        try:
            return await run_report(
                get_db(),
                user=user,
                report_id=body.report_id,
                date_from=body.date_from,
                date_to=body.date_to,
                master_item_id=body.master_item_id,
                branch_id=body.branch_id,
                warehouse_id=body.warehouse_id,
                movement_type=body.movement_type,
                reference_q=body.reference_q,
                layout=body.layout,
                page=body.page,
                page_size=body.page_size,
                include_all=body.include_all,
            )
        except StockReportError as exc:
            _raise(exc)

    @api_router.post("/laporan/persediaan/ekspor")
    async def stock_report_export(body: ReportRunBody, user=Depends(require_perm(PERM_EXPORT))):
        reject_sales_pwa_bypass(user)
        perms = set(await user_permissions(user) or [])
        if PERM_ITEM not in perms and "permissions.manage" not in perms:
            raise HTTPException(403, "Izin master_items.view diperlukan untuk ekspor Kartu Stok.")
        try:
            report = await run_report(
                get_db(),
                user=user,
                report_id=body.report_id,
                date_from=body.date_from,
                date_to=body.date_to,
                master_item_id=body.master_item_id,
                branch_id=body.branch_id,
                warehouse_id=body.warehouse_id,
                movement_type=body.movement_type,
                reference_q=body.reference_q,
                layout=body.layout,
                include_all=True,
            )
        except StockReportError as exc:
            _raise(exc)
        payload = export_workbook(report)
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="kartu-stok.xlsx"'},
        )

    @api_router.get("/laporan/persediaan/memorize")
    async def stock_report_memorize_list(user=Depends(current_user)):
        reject_sales_pwa_bypass(user)
        await _require_view(user, user_permissions)
        try:
            return {"items": await list_memorized(get_db(), user)}
        except StockReportError as exc:
            _raise(exc)

    @api_router.post("/laporan/persediaan/memorize")
    async def stock_report_memorize_save(body: MemorizeBody, user=Depends(current_user)):
        reject_sales_pwa_bypass(user)
        await _require_view(user, user_permissions)
        try:
            return await save_memorized(get_db(), user, body.model_dump())
        except StockReportError as exc:
            _raise(exc)

    @api_router.delete("/laporan/persediaan/memorize/{memorize_id}")
    async def stock_report_memorize_delete(memorize_id: str, user=Depends(current_user)):
        reject_sales_pwa_bypass(user)
        await _require_view(user, user_permissions)
        try:
            return await delete_memorized(get_db(), user, memorize_id)
        except StockReportError as exc:
            _raise(exc)
