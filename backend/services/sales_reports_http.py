"""HTTP for the Penjualan report group. Read-only besides memorized layouts."""
from __future__ import annotations

from fastapi import HTTPException, Response
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional

from services.sales_reports import (
    PERM_EXPORT,
    PERM_VIEW,
    SalesReportError,
    catalog,
    delete_memorized,
    export_workbook,
    list_memorized,
    run_report,
    save_memorized,
    user_branch_options,
)
from services.sales_user_mapping import reject_sales_pwa_bypass


class ReportRunBody(BaseModel):
    report_id: str
    date_from: str
    date_to: str
    branch: str = "semua"
    layout: Dict[str, Any] = Field(default_factory=dict)
    page: int = 1
    page_size: int = 25
    include_all: bool = False
    # Optional penjualan-per-sales filters (backward-compatible defaults = all).
    sales_user_id: str = ""
    attribution_status: str = "all"


class MemorizeBody(BaseModel):
    report_id: str
    name: str
    description: str = ""
    layout: Dict[str, Any] = Field(default_factory=dict)


def _raise(exc: SalesReportError) -> None:
    raise HTTPException(exc.status, exc.message) from exc


def register_sales_report_routes(
    api_router,
    *,
    get_db,
    require_perm,
    Depends,
):
    @api_router.get("/laporan/penjualan/katalog")
    async def sales_report_catalog(user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        db = get_db()
        from services.sales_reports import _load_user_doc

        try:
            doc = await _load_user_doc(db, user)
            branches = user_branch_options(user, doc)
            saved = await list_memorized(db, user)
        except SalesReportError as exc:
            _raise(exc)
        return {
            "group": "Penjualan",
            "reference": "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/daftar-laporan/cara-membuka-laporan-accurate-online/",
            "reports": catalog(),
            "branches": branches,
            "memorized": saved,
        }

    @api_router.post("/laporan/penjualan/tampilkan")
    async def sales_report_show(body: ReportRunBody, user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return await run_report(
                get_db(),
                user=user,
                report_id=body.report_id,
                date_from=body.date_from,
                date_to=body.date_to,
                branch=body.branch,
                layout=body.layout,
                page=body.page,
                page_size=body.page_size,
                include_all=body.include_all,
                sales_user_id=body.sales_user_id,
                attribution_status=body.attribution_status,
            )
        except SalesReportError as exc:
            _raise(exc)

    @api_router.post("/laporan/penjualan/ekspor")
    async def sales_report_export(body: ReportRunBody, user=Depends(require_perm(PERM_EXPORT))):
        reject_sales_pwa_bypass(user)
        try:
            report = await run_report(
                get_db(),
                user=user,
                report_id=body.report_id,
                date_from=body.date_from,
                date_to=body.date_to,
                branch=body.branch,
                layout=body.layout,
                include_all=True,
                sales_user_id=body.sales_user_id,
                attribution_status=body.attribution_status,
            )
        except SalesReportError as exc:
            _raise(exc)
        payload = export_workbook(report)
        filename = f"{body.report_id}.xlsx"
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @api_router.get("/laporan/penjualan/memorize")
    async def sales_report_memorize_list(user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return {"items": await list_memorized(get_db(), user)}
        except SalesReportError as exc:
            _raise(exc)

    @api_router.post("/laporan/penjualan/memorize")
    async def sales_report_memorize_save(body: MemorizeBody, user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return await save_memorized(get_db(), user, body.model_dump())
        except SalesReportError as exc:
            _raise(exc)

    @api_router.delete("/laporan/penjualan/memorize/{memorize_id}")
    async def sales_report_memorize_delete(memorize_id: str, user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return await delete_memorized(get_db(), user, memorize_id)
        except SalesReportError as exc:
            _raise(exc)
