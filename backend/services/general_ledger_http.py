"""HTTP routes for Buku Besar per akun (General Ledger Phase 1)."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query

from services.general_ledger import (
    PERM_VIEW,
    GeneralLedgerValidationError,
    get_general_ledger,
)


def register_general_ledger_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/general-ledger")
    async def get_ledger_general_ledger(
        account_id: str = Query(..., min_length=1),
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        source_type: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Buku Besar from journal_lines. Zero-write."""
        db = get_db()
        try:
            return await get_general_ledger(
                db,
                account_id=account_id,
                start_date=start_date,
                end_date=end_date,
                source_type=source_type,
                q=q,
            )
        except GeneralLedgerValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
