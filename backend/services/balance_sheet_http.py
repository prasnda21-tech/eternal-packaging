"""HTTP routes for Neraca (Balance Sheet Phase 1)."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query

from services.balance_sheet import (
    PERM_VIEW,
    BalanceSheetValidationError,
    get_balance_sheet,
)


def register_balance_sheet_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/balance-sheet")
    async def get_ledger_balance_sheet(
        as_of_date: str = Query(..., min_length=1),
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Neraca from journal_lines. Zero-write."""
        db = get_db()
        try:
            return await get_balance_sheet(
                db,
                as_of_date=as_of_date,
                q=q,
            )
        except BalanceSheetValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
