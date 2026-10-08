"""HTTP route for Dashboard Owner. GET only. Strict role + permission gate."""
from __future__ import annotations

from fastapi import HTTPException, Query

from services.owner_analytics import (
    OwnerAnalyticsAccessError,
    OwnerAnalyticsValidationError,
    PERM_VIEW,
    assert_owner_dashboard_access,
    get_owner_analytics,
)
from services.sales_dashboard import SalesDashboardValidationError
from services.sales_user_mapping import reject_sales_pwa_bypass


def register_owner_analytics_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    Depends,
):
    @api_router.get("/dashboard/owner")
    async def get_dashboard_owner(
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        preset: str = Query("custom"),
        user=Depends(current_user),
    ):
        """Read-only Owner Analytics. Admin is not a role bypass."""
        reject_sales_pwa_bypass(user)
        try:
            permissions = await user_permissions(user)
        except Exception:
            permissions = set()
        try:
            assert_owner_dashboard_access(user, permissions)
        except OwnerAnalyticsAccessError as exc:
            raise HTTPException(exc.status_code, exc.message) from exc
        db = get_db()
        try:
            return await get_owner_analytics(
                db,
                start_date=start_date,
                end_date=end_date,
                preset=preset,
                user=user,
                permissions=permissions,
            )
        except (OwnerAnalyticsValidationError, SalesDashboardValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc

    # Keep catalog key importable for tests / docs.
    get_dashboard_owner.perm = PERM_VIEW  # type: ignore[attr-defined]
