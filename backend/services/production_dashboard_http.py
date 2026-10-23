"""Read-only Production Dashboard routes. No order, stock, or finance writes."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from services.division_tasks import division_tasks_read_pipeline
from services.finance_widget_layout import LayoutError
from services.production_analytics import build_operator_analytics, build_plant_analytics
from services.production_dashboard_snapshot import build_production_snapshot
from services.production_widget_layout import get_production_layout, reset_production_layout, save_production_layout


class LayoutSaveBody(BaseModel):
    version: int = Field(..., ge=0)
    widgets: list = Field(default_factory=list)


_SNAPSHOT_FIELDS = {
    "delivery_sla_v2": 1,
    "production_schedule": 1,
    "production_schedule_events": 1,
    "production_finish_target": 1,
    "created_at": 1,
}


def _snapshot_pipeline(query: dict) -> list:
    """Task-list pipeline plus the Wave 2 fields the dashboard reads.

    The shared task projection stays unchanged. This copy is only for the
    production snapshot, so a locked delivery stamp and schedule events are
    visible without widening the division task payload.
    """
    stages = division_tasks_read_pipeline(query, limit=None)
    for stage in stages:
        if "$project" in stage:
            stage["$project"] = {**stage["$project"], **_SNAPSHOT_FIELDS}
    return stages


def _raise(exc: LayoutError) -> None:
    payload: dict = {"code": exc.code, "detail": exc.detail}
    if exc.version is not None:
        payload["version"] = exc.version
    raise HTTPException(exc.status_code, payload) from exc


def register_production_dashboard_routes(
    api_router,
    *,
    get_db,
    current_user,
    user_permissions,
    require_perm,
    apply_hold,
    attach_deadline,
    get_deadline_settings,
    now_iso,
    Depends=Depends,
):
    async def _perms(user) -> set:
        try:
            return set(await user_permissions(user))
        except Exception as exc:
            raise HTTPException(403, "Izin pengguna tidak dapat dibaca") from exc

    @api_router.get("/dashboard/produksi/snapshot")
    async def production_snapshot(user=Depends(require_perm("dashboard.production.view"))):
        """Snapshot widgets, including read-only kesiapan. Same non-cancelled population as /dashboard/summary."""
        db = get_db()
        raw = await db.orders.aggregate(
            _snapshot_pipeline({"cancelled": {"$ne": True}})
        ).to_list(None)
        orders = [order for order in raw if not order.get("cancelled")]
        settings = await get_deadline_settings()
        lead_days = settings["lead_time_days"]
        for order in orders:
            apply_hold(order)
            attach_deadline(order, lead_days)
        body = build_production_snapshot(orders, generated_at=now_iso())
        return body

    async def _analytics_orders():
        db = get_db()
        raw = await db.orders.aggregate(
            division_tasks_read_pipeline({"cancelled": {"$ne": True}}, limit=None)
        ).to_list(None)
        return [order for order in raw if not order.get("cancelled")]

    def _days(days: int) -> int:
        if days not in (7, 30):
            raise HTTPException(400, {"code": "PERIOD_INVALID", "detail": "Periode harus 7 atau 30 hari"})
        return days

    @api_router.get("/dashboard/produksi/analytics")
    async def production_analytics(days: int = 7, user=Depends(require_perm("dashboard.production.view"))):
        """Confirmed output, trend, and per-PO workflow. Read only."""
        chosen = _days(days)
        orders = await _analytics_orders()
        return build_plant_analytics(orders, days=chosen, generated_at=now_iso())

    @api_router.get("/dashboard/produksi/operator-analytics")
    async def production_operator_analytics(
        days: int = 7,
        user=Depends(require_perm("dashboard.production.operator_analytics")),
    ):
        """Actual operator output and active workload. Read only."""
        chosen = _days(days)
        orders = await _analytics_orders()
        return build_operator_analytics(orders, days=chosen, generated_at=now_iso())

    @api_router.get("/dashboard/produksi/layout")
    async def get_my_production_layout(user=Depends(current_user)):
        db = get_db()
        try:
            return await get_production_layout(db, user, await _perms(user))
        except LayoutError as exc:
            _raise(exc)

    @api_router.put("/dashboard/produksi/layout")
    async def save_my_production_layout(body: LayoutSaveBody, user=Depends(current_user)):
        db = get_db()
        try:
            return await save_production_layout(db, user, await _perms(user), body.version, body.widgets)
        except LayoutError as exc:
            _raise(exc)

    @api_router.post("/dashboard/produksi/layout/reset")
    async def reset_my_production_layout(user=Depends(current_user)):
        db = get_db()
        try:
            return await reset_production_layout(db, user, await _perms(user))
        except LayoutError as exc:
            _raise(exc)
