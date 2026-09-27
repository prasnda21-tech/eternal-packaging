"""HTTP route for Dashboard Penjualan. GET only."""
from __future__ import annotations

from fastapi import HTTPException, Query

from services.sales_dashboard import (
    PERM_VIEW,
    SalesDashboardValidationError,
    get_sales_dashboard,
)
from services.sales_user_mapping import reject_sales_pwa_bypass


def register_sales_dashboard_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    user_permissions,
    Depends,
):
    @api_router.get("/dashboard/penjualan")
    async def get_dashboard_penjualan(
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        preset: str = Query("custom"),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Read-only sales dashboard. Does not post, edit, or backfill documents."""
        reject_sales_pwa_bypass(user)
        db = get_db()
        try:
            permissions = await user_permissions(user)
        except Exception:
            permissions = set()
        try:
            return await get_sales_dashboard(
                db,
                start_date=start_date,
                end_date=end_date,
                preset=preset,
                user=user,
                permissions=permissions,
            )
        except SalesDashboardValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
