"""HTTP for the Pembelian report group. Read-only besides memorized layouts."""
from __future__ import annotations

from fastapi import HTTPException, Response
from pydantic import BaseModel, Field
from typing import Any, Dict, Optional

from services.purchase_reports import (
    PERM_EXPORT,
    PERM_VIEW,
    PurchaseReportError,
    catalog,
    delete_memorized,
    export_workbook,
    list_memorized,
    run_report,
    save_memorized,
)
from services.sales_reports import SalesReportError, user_branch_options
from services.sales_user_mapping import reject_sales_pwa_bypass


class ReportRunBody(BaseModel):
    report_id: str
    date_from: str
    date_to: str
    branch: str = "semua"
    q: str = ""
    layout: Dict[str, Any] = Field(default_factory=dict)
    page: int = 1
    page_size: int = 25
    include_all: bool = False


class MemorizeBody(BaseModel):
    report_id: str
    name: str
    description: str = ""
    layout: Dict[str, Any] = Field(default_factory=dict)


def _raise(exc: Exception) -> None:
    if isinstance(exc, (PurchaseReportError, SalesReportError)):
        raise HTTPException(exc.status, exc.message) from exc
    raise exc


def register_purchase_report_routes(
    api_router,
    *,
    get_db,
    require_perm,
    Depends,
):
    @api_router.get("/laporan/pembelian/katalog")
    async def purchase_report_catalog(user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        db = get_db()
        from services.sales_reports import _load_user_doc

        try:
            doc = await _load_user_doc(db, user)
            branches = user_branch_options(user, doc)
            saved = await list_memorized(db, user)
        except (PurchaseReportError, SalesReportError) as exc:
            _raise(exc)
        return {
            "group": "Pembelian",
            "reference": "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/daftar-laporan/cara-membuka-laporan-accurate-online/",
            "reports": catalog(),
            "branches": branches,
            "memorized": saved,
        }

    @api_router.post("/laporan/pembelian/tampilkan")
    async def purchase_report_show(body: ReportRunBody, user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return await run_report(
                get_db(),
                user=user,
                report_id=body.report_id,
                date_from=body.date_from,
                date_to=body.date_to,
                branch=body.branch,
                q=body.q,
                layout=body.layout,
                page=body.page,
                page_size=body.page_size,
                include_all=body.include_all,
            )
        except (PurchaseReportError, SalesReportError) as exc:
            _raise(exc)

    @api_router.post("/laporan/pembelian/ekspor")
    async def purchase_report_export(body: ReportRunBody, user=Depends(require_perm(PERM_EXPORT))):
        reject_sales_pwa_bypass(user)
        try:
            report = await run_report(
                get_db(),
                user=user,
                report_id=body.report_id,
                date_from=body.date_from,
                date_to=body.date_to,
                branch=body.branch,
                q=body.q,
                layout=body.layout,
                include_all=True,
            )
        except (PurchaseReportError, SalesReportError) as exc:
            _raise(exc)
        payload = export_workbook(report)
        filename = f"{body.report_id}.xlsx"
        return Response(
            content=payload,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @api_router.get("/laporan/pembelian/memorize")
    async def purchase_report_memorize_list(user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return {"items": await list_memorized(get_db(), user)}
        except (PurchaseReportError, SalesReportError) as exc:
            _raise(exc)

    @api_router.post("/laporan/pembelian/memorize")
    async def purchase_report_memorize_save(body: MemorizeBody, user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return await save_memorized(get_db(), user, body.model_dump())
        except (PurchaseReportError, SalesReportError) as exc:
            _raise(exc)

    @api_router.delete("/laporan/pembelian/memorize/{memorize_id}")
    async def purchase_report_memorize_delete(memorize_id: str, user=Depends(require_perm(PERM_VIEW))):
        reject_sales_pwa_bypass(user)
        try:
            return await delete_memorized(get_db(), user, memorize_id)
        except (PurchaseReportError, SalesReportError) as exc:
            _raise(exc)
