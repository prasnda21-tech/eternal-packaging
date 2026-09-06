"""HTTP routes for Saldo Awal / Opening Balance Phase 1."""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services.opening_balance import (
    PERM_MANAGE,
    PERM_POST,
    PERM_REVERSE,
    PERM_VIEW,
    OpeningBalanceConflictError,
    OpeningBalanceValidationError,
    create_draft,
    delete_draft,
    ensure_indexes,
    get_voucher,
    list_vouchers,
    post_voucher,
    reverse_voucher,
    update_draft,
)


class OpeningBalanceLinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = None
    account_id: str = Field(..., min_length=1)
    description: Optional[str] = ""
    debit: Any = 0
    credit: Any = 0


class OpeningBalancePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cutoff_date: str = Field(..., min_length=1)
    description: Optional[str] = ""
    notes: Optional[str] = None
    lines: List[OpeningBalanceLinePayload] = Field(..., min_length=2)


class OpeningBalanceUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cutoff_date: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    lines: Optional[List[OpeningBalanceLinePayload]] = None


def _dump(payload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def register_opening_balance_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/opening-balances")
    async def get_opening_balances(
        q: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """List Saldo Awal. Zero-write."""
        db = get_db()
        items = await list_vouchers(db, q=q, status=status)
        return {"items": items, "total": len(items)}

    @api_router.get("/ledger/opening-balances/{voucher_id}")
    async def get_opening_balance(
        voucher_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Detail + journals. Zero-write."""
        db = get_db()
        doc = await get_voucher(db, voucher_id)
        if not doc:
            raise HTTPException(404, "Saldo Awal tidak ditemukan")
        return doc

    @api_router.post("/ledger/opening-balances", status_code=201)
    async def post_opening_balance_draft(
        payload: OpeningBalancePayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            return await create_draft(db, _dump(payload), user=user)
        except OpeningBalanceValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OpeningBalanceConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/ledger/opening-balances/{voucher_id}")
    async def patch_opening_balance_draft(
        voucher_id: str,
        payload: OpeningBalanceUpdatePayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            return await update_draft(db, voucher_id, _dump(payload), user=user)
        except KeyError:
            raise HTTPException(404, "Saldo Awal tidak ditemukan") from None
        except OpeningBalanceValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OpeningBalanceConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/ledger/opening-balances/{voucher_id}")
    async def delete_opening_balance_draft(
        voucher_id: str,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            return await delete_draft(db, voucher_id)
        except KeyError:
            raise HTTPException(404, "Saldo Awal tidak ditemukan") from None
        except OpeningBalanceConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/ledger/opening-balances/{voucher_id}/post")
    async def post_opening_balance(
        voucher_id: str,
        user=Depends(require_perm(PERM_POST)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            return await post_voucher(db, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Saldo Awal tidak ditemukan") from None
        except OpeningBalanceValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OpeningBalanceConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/ledger/opening-balances/{voucher_id}/reverse")
    async def reverse_opening_balance(
        voucher_id: str,
        user=Depends(require_perm(PERM_REVERSE)),
    ):
        db = get_db()
        try:
            return await reverse_voucher(db, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Saldo Awal tidak ditemukan") from None
        except OpeningBalanceValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except OpeningBalanceConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
