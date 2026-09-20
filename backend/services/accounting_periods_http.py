"""HTTP routes for Periode Akuntansi."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict

from services.accounting_audit import list_accounting_events
from services.accounting_periods import (
    PERM_CLOSE,
    PERM_REOPEN,
    PERM_VIEW,
    PeriodConflictError,
    PeriodValidationError,
    close_period,
    ensure_indexes,
    get_period,
    list_periods,
    reopen_period,
)


class PeriodActionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Optional[str] = None


def _dump(payload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def register_accounting_periods_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/accounting-periods")
    async def get_accounting_periods(
        start: Optional[str] = Query(None),
        end: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """List months. Missing stored row is OPEN. Zero-write."""
        db = get_db()
        try:
            items = await list_periods(db, start=start, end=end)
        except PeriodValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"items": items, "total": len(items)}

    @api_router.get("/ledger/accounting-periods/{period_key}")
    async def get_accounting_period(
        period_key: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_period(db, period_key)
        except PeriodValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.post("/ledger/accounting-periods/{period_key}/close")
    async def post_close_accounting_period(
        period_key: str,
        payload: PeriodActionPayload = PeriodActionPayload(),
        user=Depends(require_perm(PERM_CLOSE)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            return await close_period(db, period_key, user=user, reason=_dump(payload).get("reason"))
        except PeriodValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PeriodConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/ledger/accounting-periods/{period_key}/reopen")
    async def post_reopen_accounting_period(
        period_key: str,
        payload: PeriodActionPayload = PeriodActionPayload(),
        user=Depends(require_perm(PERM_REOPEN)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            return await reopen_period(db, period_key, user=user, reason=_dump(payload).get("reason"))
        except PeriodValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PeriodConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.get("/ledger/accounting-audit")
    async def get_accounting_audit(
        event_type: Optional[str] = Query(None),
        entity_type: Optional[str] = Query(None),
        entity_id: Optional[str] = Query(None),
        limit: int = Query(200),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Read-only append-only accounting control events."""
        db = get_db()
        items = await list_accounting_events(
            db,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            limit=limit,
        )
        return {"items": items, "total": len(items)}
