"""HTTP routes for PO Terlambat Kirim tracking (read-only)."""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, Query

from services.branch_access import (
    branch_id_allowed,
    resolve_branch_access,
)
from services.po_late_tracking import (
    LATE_PROJECTION,
    branch_mongo_scope,
    branch_options_from_orders,
    build_late_rows,
    category_counts,
    mongo_match,
    paginate_rows,
    summary_payload,
    today_key,
)
from services.sales_user_mapping import sales_owner_match


VIEW_PERMS = (
    "dashboard.production.view",
    "delivery.schedule.view",
    "schedule.view",
    "nav.delivery_schedule",
)


def register_po_late_tracking_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_any_perm,
    get_delivery_deadline_settings,
    holiday_dates,
    now_iso,
    Depends=Depends,
):
    async def _load_scoped_orders(db, user, branch_id: Optional[str]):
        access = await resolve_branch_access(db, user)
        requested = str(branch_id or "").strip() or None
        if requested and not branch_id_allowed(access, requested):
            raise HTTPException(403, "Akses cabang ditolak")
        branch_scope = branch_mongo_scope(access, requested)
        sales_scope = sales_owner_match(user)
        match = mongo_match(sales_scope=sales_scope, branch_scope=branch_scope)
        docs = await db.orders.find(match, LATE_PROJECTION).to_list(None)
        return docs, access, sales_scope, branch_scope, requested

    @api_router.get("/delivery/po-late-tracking/summary")
    async def po_late_tracking_summary(
        branch_id: Optional[str] = None,
        user=Depends(current_user),
    ):
        await require_any_perm(user, *VIEW_PERMS)
        db = get_db()
        docs, access, sales_scope, branch_scope, requested = await _load_scoped_orders(
            db, user, branch_id,
        )
        dd = await get_delivery_deadline_settings()
        holidays = await holiday_dates(db)
        today = today_key()
        rows = build_late_rows(
            docs,
            today=today,
            lead_time_days=dd["lead_time_days"],
            holidays=holidays,
        )
        branch_options = branch_options_from_orders(docs)
        return {
            **summary_payload(
                rows,
                today=today,
                scope={
                    "branch_scope": access.get("scope"),
                    "branch_id": requested,
                    "sales_owner_scoped": bool(sales_scope),
                },
                branch_options=branch_options,
            ),
            "generated_at": now_iso(),
        }

    @api_router.get("/delivery/po-late-tracking")
    async def po_late_tracking_list(
        q: Optional[str] = None,
        category: Optional[str] = None,
        branch_id: Optional[str] = None,
        sort_by: Optional[str] = None,
        sort_dir: Optional[str] = None,
        page: Optional[int] = 1,
        page_size: Optional[int] = 50,
        user=Depends(current_user),
    ):
        """Tracking PO Terlambat Kirim — PO-grain, server-side total + page."""
        await require_any_perm(user, *VIEW_PERMS)
        db = get_db()
        docs, access, sales_scope, branch_scope, requested = await _load_scoped_orders(
            db, user, branch_id,
        )
        dd = await get_delivery_deadline_settings()
        holidays = await holiday_dates(db)
        today = today_key()
        # Active-late universe (widget-equivalent) before search/category extras.
        universe = build_late_rows(
            docs,
            today=today,
            lead_time_days=dd["lead_time_days"],
            holidays=holidays,
            branch_id=requested,
        )
        filtered = build_late_rows(
            docs,
            today=today,
            lead_time_days=dd["lead_time_days"],
            holidays=holidays,
            q=q,
            category=category,
            branch_id=requested,
            sort_by=sort_by,
            sort_dir=sort_dir,
        )
        page_rows, meta = paginate_rows(filtered, page=page or 1, page_size=page_size or 50)
        return {
            "rows": page_rows,
            "generated_at": now_iso(),
            "today": today,
            "timezone": "Asia/Jakarta",
            "grain": "po",
            "active_late_total": len(universe),
            "active_late_counts": category_counts(universe),
            "filtered_total": meta["total"],
            "counts": category_counts(filtered),
            # From already-scoped orders only — does not call /branches master.
            "branch_options": branch_options_from_orders(docs),
            "scope": {
                "branch_scope": access.get("scope"),
                "branch_id": requested,
                "sales_owner_scoped": bool(sales_scope),
            },
            **meta,
        }
