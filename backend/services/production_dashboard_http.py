"""Read-only Production Dashboard routes. No order, stock, or finance writes."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from services.branch_access import resolve_branch_access
from services.dashboard_today import wib_today
from services.division_tasks import division_tasks_read_pipeline
from services.finance_widget_layout import LayoutError
from services.po_late_tracking import branch_mongo_scope
from services.production_analytics import build_branch_output, build_operator_analytics, build_plant_analytics
from services.production_dashboard_snapshot import build_production_snapshot
from services.production_dashboard_urgent_requests import (
    PERM_MONITOR as URGENT_REQUESTS_MONITOR_PERM,
    list_dashboard_urgent_requests,
)
from services.production_schedule_scope import (
    LEAN_SCOPE_SCAN_PROJECTION,
    build_schedule_list_filter_clause,
    default_scope_candidate_clause,
    order_matches_default_production_schedule_scope,
)
from services.production_widget_layout import get_production_layout, reset_production_layout, save_production_layout
from services.sales_user_mapping import sales_owner_match
from services import urgent_requests as urg

# Per-response page size stays at the existing dashboard limit (not raised).
SCHEDULE_PAGE_LIMIT = 1000
# Internal lean scan batch — not a response cap.
_SCOPE_SCAN_BATCH = 250
# Hard stop so a pathological candidate pool cannot scan forever in one request.
_SCOPE_SCAN_DOC_CAP = 5000
# Light poll reconcile: max ids hydrated in one /dashboard/production?ids= call.
RECONCILE_IDS_PER_REQUEST = 150


def parse_reconcile_ids(raw: Optional[Any], *, limit: int = RECONCILE_IDS_PER_REQUEST) -> Tuple[List[str], bool]:
    """Parse additive reconcile ids (list or comma-separated). Preserves order, dedupes.

    Returns ``(ids_capped, truncated)``. Truncation is explicit so callers never
    silently treat an over-budget request as fully reconciled.
    """
    values: List[str] = []
    if raw is None or raw == "":
        return [], False
    if isinstance(raw, (list, tuple)):
        chunks = raw
    else:
        chunks = str(raw).split(",")
    seen = set()
    for chunk in chunks:
        oid = str(chunk or "").strip()
        if not oid or oid in seen:
            continue
        seen.add(oid)
        values.append(oid)
    cap = max(0, int(limit or 0))
    if cap <= 0:
        return [], bool(values)
    if len(values) > cap:
        return values[:cap], True
    return values, False


async def fetch_production_schedule_orders_by_ids(
    db,
    base_q: Dict[str, Any],
    projection: Optional[Dict[str, Any]],
    ids: List[str],
    *,
    limit: int = RECONCILE_IDS_PER_REQUEST,
) -> Dict[str, Any]:
    """Hydrate specific PO ids inside the caller's access/cancelled base query.

    Explicit reconcile contract:
    - ``found_ids``: returned and visible under ``base_q``
    - ``missing_ids``: requested in this batch but not visible under ``base_q``
      (cancelled when excluded, access revoked, or absent). Only these may be
      dropped by the client — never ids that were not requested.
    """
    requested, truncated = parse_reconcile_ids(ids, limit=limit)
    if not requested:
        return {
            "orders": [],
            "requested_ids": [],
            "found_ids": [],
            "missing_ids": [],
            "reconcile_truncated": bool(truncated),
            "read_limit": int(limit or 0),
            "matched_count": 0,
            "mode": "reconcile",
        }
    proj = projection if projection is not None else {"_id": 0}
    match_q = {"$and": [base_q, {"id": {"$in": requested}}]}
    docs = await db.orders.find(match_q, proj).to_list(len(requested))
    by_id = {str(d.get("id")): d for d in docs if d.get("id") is not None}
    found_ids = [oid for oid in requested if oid in by_id]
    missing_ids = [oid for oid in requested if oid not in by_id]
    orders = [by_id[oid] for oid in found_ids]
    return {
        "orders": orders,
        "requested_ids": requested,
        "found_ids": found_ids,
        "missing_ids": missing_ids,
        "reconcile_truncated": bool(truncated),
        "read_limit": int(limit or 0),
        "matched_count": len(found_ids),
        "mode": "reconcile",
    }


def wib_month_date_bounds(now: Optional[datetime] = None) -> Tuple[str, str]:
    """Inclusive YYYY-MM-DD bounds for the current Asia/Jakarta calendar month."""
    day = wib_today(now)
    start = date(day.year, day.month, 1)
    end = date(day.year, day.month, monthrange(day.year, day.month)[1])
    return start.isoformat(), end.isoformat()


def production_schedule_month_clause(month_start: str, month_end: str) -> dict:
    """Match order-level or split work_date inside the WIB month window."""
    return {
        "$or": [
            {"work_date": {"$gte": month_start, "$lte": month_end}},
            {"operator_splits.work_date": {"$gte": month_start, "$lte": month_end}},
        ]
    }


def normalize_schedule_scope(value: Optional[str]) -> str:
    raw = (value or "").strip().lower()
    return "all" if raw == "all" else "default"


def encode_schedule_cursor(scan_after_id: str) -> str:
    return str(scan_after_id or "")


def decode_schedule_cursor(cursor: Optional[str]) -> str:
    return str(cursor or "").strip()


async def _hydrate_orders_by_ids(db, ids: List[str], projection: Optional[Dict[str, Any]]) -> List[dict]:
    if not ids:
        return []
    proj = projection if projection is not None else {"_id": 0}
    docs = await db.orders.find({"id": {"$in": ids}}, proj).to_list(len(ids))
    by_id = {str(d.get("id")): d for d in docs if d.get("id") is not None}
    return [by_id[i] for i in ids if i in by_id]


def _merge_base_with_list_filters(
    base_q: Dict[str, Any],
    list_filter: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not list_filter:
        return dict(base_q)
    return {"$and": [base_q, list_filter]}


async def fetch_production_schedule_orders_page(
    db,
    base_q: Dict[str, Any],
    projection: Optional[Dict[str, Any]],
    *,
    limit: int = SCHEDULE_PAGE_LIMIT,
    schedule_scope: str = "default",
    cursor: Optional[str] = None,
    now: Optional[datetime] = None,
    q: Optional[str] = None,
    work_date_from: Optional[str] = None,
    work_date_to: Optional[str] = None,
    deadline_from: Optional[str] = None,
    deadline_to: Optional[str] = None,
    refresh: bool = False,
) -> Dict[str, Any]:
    """Return one page of schedule rows with qty-based default scope (not stage).

    ``schedule_scope=default``: scan candidate pool ordered by ``id``, keep rows
    that match work_date/qty unfinished semantics, hydrate up to ``limit``.
    ``schedule_scope=all``: id-ordered window over the filtered catalog (histori).

    Additive list filters (``q``, work_date / deadline ranges) narrow ``base_q``
    without changing access/cancelled rules. ``refresh=True`` still returns one
    page from the start cursor so clients can reconcile without restaging all
    history.

    Never claims ``scope_complete`` while ``next_cursor`` remains. Population
    ``matched_count`` is the filtered catalog size so callers can warn when the
    read window is incomplete.
    """
    cap = max(0, int(limit or 0))
    scope = normalize_schedule_scope(schedule_scope)
    list_filter = build_schedule_list_filter_clause(
        q=q,
        work_date_from=work_date_from,
        work_date_to=work_date_to,
        deadline_from=deadline_from,
        deadline_to=deadline_to,
    )
    effective_base = _merge_base_with_list_filters(base_q, list_filter)
    population_matched = await db.orders.count_documents(effective_base)
    if cap <= 0:
        return {
            "orders": [],
            "matched_count": population_matched,
            "candidate_count": 0,
            "next_cursor": None,
            "scope_complete": True,
            "schedule_scope": scope,
            "read_limit": 0,
            "scan_capped": False,
            "refresh": bool(refresh),
        }

    today = wib_today(now)
    today_key = today.isoformat()
    month_start, month_end = wib_month_date_bounds(now)
    # Refresh snapshots always restart from the head of the stream.
    scan_after = "" if refresh else decode_schedule_cursor(cursor)

    if scope == "all":
        page_q: Dict[str, Any] = dict(effective_base)
        if scan_after:
            page_q = {"$and": [effective_base, {"id": {"$gt": scan_after}}]}
        proj = projection if projection is not None else {"_id": 0}
        # Stable id order for cursor paging (deadline alone is not unique).
        docs = await db.orders.find(page_q, proj).sort([("id", 1)]).to_list(cap + 1)
        page_docs = docs[:cap]
        has_more = len(docs) > cap
        next_cursor = encode_schedule_cursor(str(page_docs[-1].get("id"))) if has_more and page_docs else None
        return {
            "orders": page_docs,
            "matched_count": population_matched,
            "candidate_count": population_matched,
            "next_cursor": next_cursor,
            "scope_complete": not has_more,
            "schedule_scope": scope,
            "read_limit": cap,
            "scan_capped": False,
            "refresh": bool(refresh),
        }

    candidate_q = {
        "$and": [effective_base, default_scope_candidate_clause(month_start, month_end)],
    }
    candidate_count = await db.orders.count_documents(candidate_q)

    matched_ids: List[str] = []
    scanned = 0
    last_scan_id = scan_after
    exhausted = False
    scan_capped = False
    while len(matched_ids) < cap and scanned < _SCOPE_SCAN_DOC_CAP:
        batch_q: Dict[str, Any] = candidate_q
        if last_scan_id:
            batch_q = {"$and": [candidate_q, {"id": {"$gt": last_scan_id}}]}
        batch = await db.orders.find(batch_q, LEAN_SCOPE_SCAN_PROJECTION).sort("id", 1).to_list(
            _SCOPE_SCAN_BATCH
        )
        if not batch:
            exhausted = True
            break
        stopped_for_page = False
        for lean in batch:
            scanned += 1
            oid = lean.get("id")
            if oid is None:
                continue
            last_scan_id = str(oid)
            if order_matches_default_production_schedule_scope(lean, today_key=today_key):
                matched_ids.append(str(oid))
                if len(matched_ids) >= cap:
                    stopped_for_page = True
                    break
        if stopped_for_page:
            # Remaining docs in this batch were not examined — peek decides has_more.
            break
        if len(batch) < _SCOPE_SCAN_BATCH:
            exhausted = True
            break
        if scanned >= _SCOPE_SCAN_DOC_CAP:
            scan_capped = True
            break

    # Peek whether more *matching* candidates remain (lean projection required).
    has_more = False
    if exhausted:
        has_more = False
    elif scan_capped:
        has_more = True
    elif last_scan_id:
        peek_after = last_scan_id
        peek_scanned = 0
        while peek_scanned < _SCOPE_SCAN_DOC_CAP:
            peek_q = {"$and": [candidate_q, {"id": {"$gt": peek_after}}]}
            peek = await db.orders.find(peek_q, LEAN_SCOPE_SCAN_PROJECTION).sort("id", 1).to_list(
                _SCOPE_SCAN_BATCH
            )
            if not peek:
                exhausted = True
                break
            for lean in peek:
                peek_scanned += 1
                oid = lean.get("id")
                if oid is not None:
                    peek_after = str(oid)
                if order_matches_default_production_schedule_scope(lean, today_key=today_key):
                    has_more = True
                    break
            if has_more:
                break
            if len(peek) < _SCOPE_SCAN_BATCH:
                exhausted = True
                break
        if not has_more and not exhausted and peek_scanned >= _SCOPE_SCAN_DOC_CAP:
            # More candidates may remain beyond the peek budget — keep cursor.
            has_more = True
            scan_capped = True

    orders = await _hydrate_orders_by_ids(db, matched_ids, projection)
    next_cursor = encode_schedule_cursor(last_scan_id) if has_more and last_scan_id else None
    scope_complete = next_cursor is None
    return {
        "orders": orders,
        "matched_count": population_matched,
        "candidate_count": candidate_count,
        "next_cursor": next_cursor,
        "scope_complete": scope_complete,
        "schedule_scope": scope,
        "read_limit": cap,
        "scan_capped": bool(scan_capped),
        "refresh": bool(refresh),
    }


async def fetch_production_schedule_orders(
    db,
    base_q: Dict[str, Any],
    projection: Optional[Dict[str, Any]],
    limit: int,
    now: Optional[datetime] = None,
) -> Tuple[List[dict], int]:
    """ Backward-compatible single-page helper (default scope, no cursor)."""
    page = await fetch_production_schedule_orders_page(
        db,
        base_q,
        projection,
        limit=limit,
        schedule_scope="default",
        cursor=None,
        now=now,
    )
    return page["orders"], int(page["matched_count"])


# Hard cap for Excel export — never silently truncate past this size.
PRODUCTION_EXPORT_HARD_LIMIT = 2000
_EXPORT_PAGE_SIZE = 500

EXPORT_ERROR_TOO_LARGE = "EXPORT_TOO_LARGE"
EXPORT_ERROR_INCOMPLETE = "EXPORT_INCOMPLETE"
EXPORT_ERROR_IDS_DENIED = "EXPORT_IDS_DENIED"


class ProductionExportTooLarge(Exception):
    """Raised when export would exceed PRODUCTION_EXPORT_HARD_LIMIT."""

    code = EXPORT_ERROR_TOO_LARGE

    def __init__(self, limit: int = PRODUCTION_EXPORT_HARD_LIMIT):
        self.limit = int(limit)
        self.message = (
            f"Ekspor dibatalkan: hasil melebihi batas aman {self.limit} PO. "
            "Persempit cakupan atau filter, lalu coba lagi."
        )
        super().__init__(self.message)

    def http_detail(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message}


class ProductionExportIncomplete(Exception):
    """Raised when the server cannot prove the export set is complete/authorized."""

    def __init__(self, message: str, *, code: str = EXPORT_ERROR_INCOMPLETE):
        self.code = str(code or EXPORT_ERROR_INCOMPLETE)
        self.message = str(message or "Ekspor dibatalkan: hasil belum lengkap.")
        super().__init__(self.message)

    def http_detail(self) -> Dict[str, Any]:
        return {"code": self.code, "message": self.message}


def production_schedule_export_shipping_date(order: dict) -> Optional[str]:
    """Same fallback chain as desktop sheet Tanggal Pengiriman (no legacy deadline)."""
    if not order:
        return None
    return (
        order.get("tanggal_pengiriman")
        or order.get("effective_delivery_date")
        or order.get("delivery_deadline_effective")
        or order.get("scheduled_delivery_date")
        or order.get("delivery_deadline_manual")
        or None
    )


async def collect_production_export_orders(
    db,
    base_q: Dict[str, Any],
    *,
    schedule_scope: str = "default",
    ids: Optional[List[str]] = None,
    hard_limit: int = PRODUCTION_EXPORT_HARD_LIMIT,
    now: Optional[datetime] = None,
) -> List[dict]:
    """Collect export orders under Jadwal Produksi scope. Server is completeness authority.

    ``ids`` path (Export Terfilter): every requested id must be visible under
    ``base_q`` (permission/cancelled) and match ``schedule_scope``. Missing or
    out-of-scope ids fail closed — never silently omitted from a successful file.

    Scope path (Export Semua): page until ``scope_complete`` without scan cap /
    residual cursor. Incomplete collections raise ``ProductionExportIncomplete``.

    Does not trust client completeness flags. Client filter columns beyond
    schedule_scope are not re-derived here; Terfilter only attests that each
    supplied id is authorized and in schedule_scope.
    """
    cap = max(0, int(hard_limit or 0))
    scope = normalize_schedule_scope(schedule_scope)
    if ids is not None:
        requested: List[str] = []
        seen = set()
        for raw in ids:
            oid = str(raw or "").strip()
            if not oid or oid in seen:
                continue
            seen.add(oid)
            requested.append(oid)
        if not requested:
            return []
        if len(requested) > cap:
            raise ProductionExportTooLarge(cap)
        page = await fetch_production_schedule_orders_by_ids(
            db,
            base_q,
            {"_id": 0},
            requested,
            limit=max(len(requested), 1),
        )
        if page.get("reconcile_truncated"):
            raise ProductionExportIncomplete(
                "Ekspor dibatalkan: daftar id terfilter melebihi batas rekonsiliasi server. "
                "Persempit filter, lalu coba lagi.",
                code=EXPORT_ERROR_INCOMPLETE,
            )
        by_id = {str(o.get("id")): o for o in page.get("orders") or [] if o.get("id") is not None}
        today_key = wib_today(now).isoformat()
        denied = False
        ordered: List[dict] = []
        for oid in requested:
            order = by_id.get(oid)
            if order is None:
                denied = True
                break
            if scope == "default" and not order_matches_default_production_schedule_scope(
                order, today_key=today_key,
            ):
                denied = True
                break
            ordered.append(order)
        if denied:
            # Do not echo which ids failed — avoids leaking existence across ACL/scope.
            raise ProductionExportIncomplete(
                "Ekspor dibatalkan: sebagian PO tidak diizinkan atau di luar cakupan jadwal. "
                "Refresh daftar lengkap, lalu ekspor lagi.",
                code=EXPORT_ERROR_IDS_DENIED,
            )
        return ordered

    collected: List[dict] = []
    cursor: Optional[str] = None
    last: Optional[Dict[str, Any]] = None
    while True:
        page = await fetch_production_schedule_orders_page(
            db,
            base_q,
            {"_id": 0},
            limit=_EXPORT_PAGE_SIZE,
            schedule_scope=scope,
            cursor=cursor,
            now=now,
        )
        last = page
        batch = list(page.get("orders") or [])
        for order in batch:
            collected.append(order)
            if len(collected) > cap:
                raise ProductionExportTooLarge(cap)
        next_cursor = page.get("next_cursor") or None
        scan_capped = bool(page.get("scan_capped"))
        scope_complete = bool(page.get("scope_complete"))
        if scope_complete and not next_cursor and not scan_capped:
            break
        if not next_cursor:
            break
        if scan_capped:
            # Residual unread candidates — stop and fail closed below.
            break
        cursor = str(next_cursor)

    if (
        last is None
        or not bool(last.get("scope_complete"))
        or bool(last.get("scan_capped"))
        or bool(last.get("next_cursor"))
    ):
        raise ProductionExportIncomplete(
            "Ekspor dibatalkan: hasil cakupan jadwal belum lengkap di server "
            "(batas baca/scan atau cursor tersisa). Persempit cakupan atau coba lagi.",
            code=EXPORT_ERROR_INCOMPLETE,
        )
    return collected


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
    require_opt_in_perm=None,
    Depends=Depends,
):
    async def _perms(user) -> set:
        try:
            return set(await user_permissions(user))
        except Exception as exc:
            raise HTTPException(403, "Izin pengguna tidak dapat dibaca") from exc

    async def _require_urgent_monitor(user) -> None:
        """Dashboard monitor perm, or urgent.review/approve. Never grants mutate rights."""
        perms = await _perms(user)
        if URGENT_REQUESTS_MONITOR_PERM in perms:
            return
        if require_opt_in_perm is None:
            raise HTTPException(500, "Pemeriksaan izin urgent tidak tersedia")
        try:
            await require_opt_in_perm(user, urg.PERM_REVIEW)
            return
        except HTTPException as review_exc:
            if review_exc.status_code != 403:
                raise
        await require_opt_in_perm(user, urg.PERM_APPROVE)

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

    @api_router.get("/dashboard/produksi/urgent-requests")
    async def production_urgent_requests(
        status: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(10, ge=1, le=50),
        user=Depends(require_perm("dashboard.production.view")),
    ):
        """Read-only urgent request table. Scoped like other produksi widgets."""
        await _require_urgent_monitor(user)
        db = get_db()
        perms = await _perms(user)
        return await list_dashboard_urgent_requests(
            db,
            user,
            status=status,
            q=q,
            page=page,
            page_size=page_size,
            can_open_po="po.view" in perms,
            now_iso=now_iso(),
        )

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
