"""HTTP routes for Neraca Saldo (Trial Balance Phase 1)."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query

from services.trial_balance import (
    PERM_VIEW,
    TrialBalanceValidationError,
    get_trial_balance,
)


def register_trial_balance_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/trial-balance")
    async def get_ledger_trial_balance(
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Neraca Saldo from journal_lines. Zero-write."""
        db = get_db()
        try:
            return await get_trial_balance(
                db,
                start_date=start_date,
                end_date=end_date,
                q=q,
            )
        except TrialBalanceValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
