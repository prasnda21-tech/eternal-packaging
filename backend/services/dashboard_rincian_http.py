"""HTTP registration for GET /dashboard/rincian."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query


def register_dashboard_rincian_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    apply_hold,
    attach_deadline,
    get_deadline_settings,
    status_po_projection,
    sales_owner_match,
    list_delivery_schedule,
    query_sales_invoices,
    list_purchase_invoices,
    get_mutasi,
    list_cash_banks,
    get_profit_loss,
    build_kepala_board_for_user,
    Depends=Depends,
):
    from services.dashboard_rincian import query_dashboard_rincian
    from services.production_dashboard_http import order_branch_scope

    @api_router.get("/dashboard/rincian")
    async def dashboard_rincian(
        widget: str = Query(..., min_length=1),
        dashboard: str = Query(""),
        q: str = Query(""),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=200),
        start_date: Optional[str] = Query(None),
        end_date: Optional[str] = Query(None),
        unit: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        cash_bank_id: Optional[str] = Query(None),
        basis: Optional[str] = Query(None),
        user=Depends(current_user),
    ):
        """Spreadsheet detail for one dashboard KPI card. Read-only."""
        basis_key = (basis or "").strip().lower()
        if basis_key not in ("", "snapshot"):
            raise HTTPException(400, "basis tidak dikenali")
        db = get_db()
        try:
            perms = set(await user_permissions(user))
        except Exception as exc:
            raise HTTPException(403, "Izin pengguna tidak dapat dibaca") from exc
        settings = await get_deadline_settings()
        lead_days = settings.get("lead_time_days") or settings.get("sla_working_days") or 3
        from services.production_wave2 import holiday_dates
        holidays = await holiday_dates(db)

        async def _schedule(_db):
            return await list_delivery_schedule(user)

        async def _kepala():
            return await build_kepala_board_for_user(db, user, lead_days=int(lead_days), holidays=holidays)

        async def _banks(_db):
            items = await list_cash_banks(_db, active_only=True)
            return items if isinstance(items, list) else items

        async def _purchases(_db, **kwargs):
            return await list_purchase_invoices(_db, **kwargs)

        async def _mutasi(_db, **kwargs):
            return await get_mutasi(_db, **kwargs)

        async def _pl(_db, **kwargs):
            return await get_profit_loss(_db, **kwargs)

        async def _sales(_db, **kwargs):
            return await query_sales_invoices(_db, **kwargs)

        async def _branch_scope(db_conn, actor):
            return await order_branch_scope(db_conn, actor)

        return await query_dashboard_rincian(
            db,
            widget=widget,
            dashboard=dashboard or "",
            permissions=perms,
            q=q or "",
            page=page,
            page_size=page_size,
            start_date=(start_date or "").strip(),
            end_date=(end_date or "").strip(),
            unit=(unit or "").strip(),
            status=(status or "").strip(),
            cash_bank_id=(cash_bank_id or "").strip(),
            apply_hold=apply_hold,
            attach_deadline=attach_deadline,
            lead_days=int(lead_days),
            holidays=holidays,
            sales_scope=sales_owner_match(user) if callable(sales_owner_match) else None,
            branch_scope=await _branch_scope(db, user),
            status_po_projection=status_po_projection,
            list_delivery_schedule_rows=_schedule,
            query_sales_invoices=_sales,
            list_purchase_invoices=_purchases,
            get_mutasi=_mutasi,
            get_profit_loss=_pl,
            list_cash_banks=_banks,
            build_kepala_board=_kepala,
        )
