"""HTTP routes for Jurnal Umum (General Journal) Phase 1."""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from services.general_journal import (
    PERM_MANAGE,
    PERM_POST,
    PERM_REVERSE,
    PERM_VIEW,
    GeneralJournalConflictError,
    GeneralJournalValidationError,
    create_draft,
    delete_draft,
    ensure_indexes,
    get_voucher,
    list_vouchers,
    post_voucher,
    reverse_voucher,
    update_draft,
)


class JournalLinePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: Optional[str] = None
    account_id: str = Field(..., min_length=1)
    description: Optional[str] = ""
    debit: Any = 0
    credit: Any = 0


class GeneralJournalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = Field(..., min_length=1)
    description: Optional[str] = ""
    lines: List[JournalLinePayload] = Field(..., min_length=2)


class GeneralJournalUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: Optional[str] = None
    description: Optional[str] = None
    lines: Optional[List[JournalLinePayload]] = None


def _dump(payload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    return payload.dict(exclude_unset=True)


def register_general_journal_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/general-journals")
    async def get_general_journals(
        q: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """List Jurnal Umum. Zero-write."""
        db = get_db()
        items = await list_vouchers(db, q=q, status=status)
        return {"items": items, "total": len(items)}

    @api_router.get("/ledger/general-journals/{voucher_id}")
    async def get_general_journal(
        voucher_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Detail + journals. Zero-write."""
        db = get_db()
        doc = await get_voucher(db, voucher_id)
        if not doc:
            raise HTTPException(404, "Jurnal Umum tidak ditemukan")
        return doc

    @api_router.post("/ledger/general-journals", status_code=201)
    async def post_general_journal_draft(
        payload: GeneralJournalPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            return await create_draft(db, _dump(payload), user=user)
        except GeneralJournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except GeneralJournalConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/ledger/general-journals/{voucher_id}")
    async def patch_general_journal_draft(
        voucher_id: str,
        payload: GeneralJournalUpdatePayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            return await update_draft(db, voucher_id, _dump(payload), user=user)
        except KeyError:
            raise HTTPException(404, "Jurnal Umum tidak ditemukan") from None
        except GeneralJournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except GeneralJournalConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/ledger/general-journals/{voucher_id}")
    async def delete_general_journal_draft(
        voucher_id: str,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            return await delete_draft(db, voucher_id)
        except KeyError:
            raise HTTPException(404, "Jurnal Umum tidak ditemukan") from None
        except GeneralJournalConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/ledger/general-journals/{voucher_id}/post")
    async def post_general_journal(
        voucher_id: str,
        user=Depends(require_perm(PERM_POST)),
    ):
        db = get_db()
        await ensure_indexes(db)
        try:
            return await post_voucher(db, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Jurnal Umum tidak ditemukan") from None
        except GeneralJournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except GeneralJournalConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/ledger/general-journals/{voucher_id}/reverse")
    async def reverse_general_journal(
        voucher_id: str,
        user=Depends(require_perm(PERM_REVERSE)),
    ):
        db = get_db()
        try:
            return await reverse_voucher(db, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Jurnal Umum tidak ditemukan") from None
        except GeneralJournalValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except GeneralJournalConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
