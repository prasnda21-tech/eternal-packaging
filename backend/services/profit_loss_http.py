"""HTTP routes for Laba/Rugi (Profit & Loss Phase 1)."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query

from services.profit_loss import (
    PERM_VIEW,
    ProfitLossValidationError,
    get_profit_loss,
)


def register_profit_loss_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/profit-loss")
    async def get_ledger_profit_loss(
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Laba/Rugi from journal_lines. Zero-write."""
        db = get_db()
        try:
            return await get_profit_loss(
                db,
                start_date=start_date,
                end_date=end_date,
                q=q,
            )
        except ProfitLossValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
