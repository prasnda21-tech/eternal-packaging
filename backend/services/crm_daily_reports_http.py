"""HTTP routes for CRM Daily Reports."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import Query
from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_crm_daily_report_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    require_any_perm,
    Depends,
):
    from services import crm_daily_reports as svc

    async def _perms(user: dict) -> set:
        return await user_permissions(user)

    @api_router.get("/crm-daily-reports/customers")
    async def crm_daily_report_customers(
        q: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(
            user,
            svc.PERM_CREATE,
            svc.PERM_EDIT_OWN,
            svc.PERM_VIEW_OWN,
            svc.PERM_VIEW_ALL,
        )
        return {"customers": await svc.search_customers_for_report(get_db(), q=q or "")}

    @api_router.get("/crm-daily-reports/orders")
    async def crm_daily_report_orders(
        q: Optional[str] = None,
        customer_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        perms = await _perms(user)
        await require_any_perm(
            user,
            svc.PERM_CREATE,
            svc.PERM_EDIT_OWN,
            svc.PERM_VIEW_OWN,
            svc.PERM_VIEW_ALL,
        )
        return {
            "orders": await svc.search_orders_for_report(
                get_db(),
                q=q or "",
                customer_id=customer_id or "",
                perms=perms,
            )
        }

    @api_router.get("/crm-daily-reports/sales")
    async def crm_daily_report_sales(
        q: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(
            user,
            svc.PERM_CREATE,
            svc.PERM_EDIT_OWN,
            svc.PERM_VIEW_OWN,
            svc.PERM_VIEW_ALL,
        )
        return {"sales": await svc.list_sales_for_report(get_db(), q=q or "")}

    @api_router.get("/crm-daily-reports/meta")
    async def crm_daily_report_meta(user=Depends(current_user)):
        perms = await _perms(user)
        await require_any_perm(
            user,
            svc.PERM_CREATE,
            svc.PERM_VIEW_OWN,
            svc.PERM_EDIT_OWN,
            svc.PERM_VIEW_ALL,
        )
        return {
            "today_wib": svc.today_wib_date(),
            "can_create": svc.can_create(perms),
            "can_view_all": svc.can_view_all(perms),
            "can_edit_own": svc.can_edit_own(perms),
            "reporter": {
                "id": svc.actor_user_id(user),
                "name": svc.actor_name(user),
            },
            **svc.excel_dropdown_meta(),
        }

    @api_router.get("/crm-daily-reports")
    async def list_crm_daily_reports(
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        crm_user_id: Optional[str] = None,
        sales: Optional[str] = None,
        status_order: Optional[str] = None,
        status_customer: Optional[str] = None,
        has_complaint: Optional[str] = None,
        has_issue: Optional[str] = None,
        q: Optional[str] = None,
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=100),
        user=Depends(current_user),
    ):
        perms = await _perms(user)
        await require_any_perm(user, svc.PERM_VIEW_OWN, svc.PERM_VIEW_ALL, svc.PERM_CREATE, svc.PERM_EDIT_OWN)
        return await svc.list_reports(
            get_db(),
            user,
            perms,
            filters={
                "date_from": date_from,
                "date_to": date_to,
                "crm_user_id": crm_user_id,
                "sales": sales,
                "status_order": status_order,
                "status_customer": status_customer,
                "has_complaint": has_complaint if has_complaint is not None else "",
                "has_issue": has_issue if has_issue is not None else "",
                "q": q,
            },
            page=page,
            page_size=page_size,
        )

    @api_router.post("/crm-daily-reports")
    async def create_crm_daily_report(
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, svc.PERM_CREATE)
        await svc.ensure_indexes(get_db())
        perms = await _perms(user)
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"report": await svc.create_report(get_db(), data, user, perms)}

    @api_router.get("/crm-daily-reports/{report_id}")
    async def get_crm_daily_report(report_id: str, user=Depends(current_user)):
        perms = await _perms(user)
        await require_any_perm(user, svc.PERM_VIEW_OWN, svc.PERM_VIEW_ALL, svc.PERM_CREATE, svc.PERM_EDIT_OWN)
        return {"report": await svc.get_report(get_db(), report_id, user, perms)}

    @api_router.patch("/crm-daily-reports/{report_id}")
    async def update_crm_daily_report(
        report_id: str,
        payload: Optional[LoosePayload] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, svc.PERM_EDIT_OWN)
        perms = await _perms(user)
        data: dict[str, Any] = payload.model_dump() if payload else {}
        return {"report": await svc.update_report(get_db(), report_id, data, user, perms)}
