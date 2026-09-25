"""HTTP route for Dashboard Keuangan. GET only."""
from __future__ import annotations

from fastapi import HTTPException, Query

from services.financial_dashboard import (
    PERM_VIEW,
    FinancialDashboardValidationError,
    get_financial_dashboard,
)


def register_financial_dashboard_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    user_permissions,
    Depends,
):
    @api_router.get("/dashboard/keuangan")
    async def get_dashboard_keuangan(
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Read-only financial dashboard. Does not post or write books."""
        db = get_db()
        try:
            permissions = await user_permissions(user)
        except Exception:
            permissions = set()
        try:
            return await get_financial_dashboard(
                db,
                start_date=start_date,
                end_date=end_date,
                permissions=permissions,
            )
        except FinancialDashboardValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
