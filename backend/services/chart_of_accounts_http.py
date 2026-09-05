"""HTTP routes for Chart of Accounts (Akun Perkiraan)."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services.chart_of_accounts import (
    PERM_MANAGE,
    PERM_VIEW,
    AccountConflictError,
    AccountInUseError,
    AccountValidationError,
    create_account,
    delete_account,
    ensure_indexes,
    list_accounts,
)


class AccountCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


def register_chart_of_accounts_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/accounts")
    async def get_ledger_accounts(
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """List accounts. Zero-write: never seeds or mutates."""
        db = get_db()
        items = await list_accounts(db, q=q)
        return {"items": items, "total": len(items)}

    @api_router.post("/ledger/accounts", status_code=201)
    async def post_ledger_account(
        payload: AccountCreatePayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            doc = await create_account(db, payload.model_dump() if hasattr(payload, "model_dump") else payload.dict(), user=user)
        except AccountValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except AccountConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return doc

    @api_router.delete("/ledger/accounts/{account_id}")
    async def delete_ledger_account(
        account_id: str,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            deleted = await delete_account(db, account_id)
        except KeyError:
            raise HTTPException(404, "Akun tidak ditemukan") from None
        except AccountInUseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "deleted": deleted}
