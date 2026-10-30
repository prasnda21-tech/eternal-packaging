"""Read-only Production Dashboard routes. No order, stock, or finance writes."""
from __future__ import annotations

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field

from services.branch_access import resolve_branch_access
from services.division_tasks import division_tasks_read_pipeline
from services.finance_widget_layout import LayoutError
from services.po_late_tracking import branch_mongo_scope
from services.production_analytics import build_branch_output, build_operator_analytics, build_plant_analytics
from services.production_dashboard_snapshot import build_production_snapshot
from services.production_widget_layout import get_production_layout, reset_production_layout, save_production_layout
from services.sales_user_mapping import sales_owner_match


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
_ANALYTICS_FIELDS = {"branch_id": 1, "operational_branch_id": 1}


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


async def order_branch_scope(db, user) -> dict:
    """Origin-branch fragment from the PO Late Tracking resolver.

    Empty for Direktur, Holding, and other global scopes. Assigned users and
    assignments use the internal operational branch. Role admin is not a bypass.
    """
    from services.operational_branch import operational_query
    return await operational_query(db, user)


async def order_access_scope(db, user) -> dict:
    """Branch fragment plus sales ownership. Does not set cancelled."""
    scope = await order_branch_scope(db, user)
    scope.update(sales_owner_match(user))
    return scope


def scoped_read_meta(matched_count: int, returned: int, limit: int) -> dict:
    """Honest read window. A capped list is not the scoped population."""
    incomplete = int(matched_count) > int(returned)
    return {
        "read_limit": limit,
        "matched_count": int(matched_count),
        "completeness": "incomplete" if incomplete else "complete",
    }


async def order_population_query(db, user) -> dict:
    """Non-cancelled PO population in the caller's origin-branch scope.

    Same resolver as PO Late Tracking. Callers pass this dict as the
    aggregation ``$match`` before any dashboard math. KPI builders are
    unchanged. Buffer stock stays in this population; late tracking still
    excludes it on its own route. ``sales_owner_match`` is empty unless the
    role is sales_eternal, and that role remains surface-locked off these
    routes.
    """
    query = {"cancelled": {"$ne": True}}
    query.update(await order_access_scope(db, user))
    return query


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
        """Snapshot widgets. Non-cancelled orders in the caller's PO-origin branch scope."""
        db = get_db()
        raw = await db.orders.aggregate(
            _snapshot_pipeline(await order_population_query(db, user))
        ).to_list(None)
        orders = [order for order in raw if not order.get("cancelled")]
        settings = await get_deadline_settings()
        lead_days = settings["sla_working_days"]
        from services.production_wave2 import holiday_dates
        holidays = await holiday_dates(db)
        for order in orders:
            apply_hold(order)
            attach_deadline(order, lead_days, holidays=holidays)
        body = build_production_snapshot(
            orders,
            generated_at=now_iso(),
            sla_working_days=lead_days,
            holidays=holidays,
        )
        return body

    def _analytics_pipeline(query: dict) -> list:
        stages = division_tasks_read_pipeline(query, limit=None)
        for stage in stages:
            if "$project" in stage:
                stage["$project"] = {**stage["$project"], **_ANALYTICS_FIELDS}
        return stages

    async def _analytics_orders(user):
        db = get_db()
        raw = await db.orders.aggregate(
            _analytics_pipeline(await order_population_query(db, user))
        ).to_list(None)
        return [{**order, "branch_id": order.get("operational_branch_id") or order.get("branch_id")}
                for order in raw if not order.get("cancelled")]

    def _days(days: int) -> int:
        if days not in (7, 30):
            raise HTTPException(400, {"code": "PERIOD_INVALID", "detail": "Periode harus 7 atau 30 hari"})
        return days

    @api_router.get("/dashboard/produksi/analytics")
    async def production_analytics(days: int = 7, user=Depends(require_perm("dashboard.production.view"))):
        """Confirmed output, trend, and per-PO workflow. Read only."""
        chosen = _days(days)
        orders = await _analytics_orders(user)
        return build_plant_analytics(orders, days=chosen, generated_at=now_iso())

    @api_router.get("/dashboard/produksi/operator-analytics")
    async def production_operator_analytics(
        days: int = 7,
        user=Depends(require_perm("dashboard.production.operator_analytics")),
    ):
        """Actual operator output and active workload. Read only."""
        chosen = _days(days)
        orders = await _analytics_orders(user)
        return build_operator_analytics(orders, days=chosen, generated_at=now_iso())

    @api_router.get("/dashboard/produksi/branch-output")
    async def production_branch_output(
        days: int = 7,
        user=Depends(require_perm("dashboard.production.branch_output")),
    ):
        """Actual production location and PO origin. Read only."""
        chosen = _days(days)
        db = get_db()
        orders = await _analytics_orders(user)
        docs = await db.branches.find({}, {"_id": 0, "id": 1, "name": 1}).to_list(500)
        names = {
            str(doc.get("id")): str(doc.get("name") or doc.get("id"))
            for doc in docs
            if doc.get("id")
        }
        return build_branch_output(orders, branch_names=names, days=chosen, generated_at=now_iso())

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
