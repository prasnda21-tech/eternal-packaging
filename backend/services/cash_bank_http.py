"""HTTP routes for Kas & Bank Phase 1."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import HTTPException, Query
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field

from services import cash_bank_attachment as attachment_svc
from services.cash_bank_master import (
    PERM_MANAGE,
    PERM_POST,
    PERM_REVERSE,
    PERM_VIEW,
    CashBankConflictError,
    CashBankValidationError,
    create_cash_bank,
    ensure_indexes as ensure_master_indexes,
    get_cash_bank,
    list_cash_banks,
    update_cash_bank,
)
from services.cash_bank_vouchers import (
    KIND_PAYMENT,
    KIND_RECEIPT,
    KIND_TRANSFER,
    VoucherConflictError,
    VoucherValidationError,
    create_payment,
    create_receipt,
    create_transfer,
    delete_draft,
    ensure_indexes as ensure_voucher_indexes,
    get_voucher,
    get_voucher_with_journal,
    list_vouchers,
    post_voucher,
    reverse_voucher,
    set_voucher_attachment,
    update_draft,
)
from services.journal_core import ensure_indexes as ensure_journal_indexes


class CashBankCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    description: Optional[str] = ""
    active: bool = True


class CashBankUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    account_id: Optional[str] = None


class ReceiptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = Field(..., min_length=1)
    cash_bank_id: str = Field(..., min_length=1)
    counter_account_id: str = Field(..., min_length=1)
    amount: Any
    description: Optional[str] = ""
    reference: Optional[str] = ""
    attachment_data_url: Optional[str] = None
    attachment_filename: Optional[str] = None


class PaymentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = Field(..., min_length=1)
    cash_bank_id: str = Field(..., min_length=1)
    counter_account_id: str = Field(..., min_length=1)
    amount: Any
    description: Optional[str] = ""
    reference: Optional[str] = ""
    attachment_data_url: Optional[str] = None
    attachment_filename: Optional[str] = None


class TransferPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: str = Field(..., min_length=1)
    source_cash_bank_id: str = Field(..., min_length=1)
    dest_cash_bank_id: str = Field(..., min_length=1)
    amount: Any
    description: Optional[str] = ""
    reference: Optional[str] = ""


class AttachmentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_url: str = Field(..., min_length=1)
    filename: Optional[str] = "lampiran"


def _dump(payload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump()
    return payload.dict()


async def _ensure_all(db) -> None:
    await ensure_master_indexes(db)
    await ensure_voucher_indexes(db)
    await ensure_journal_indexes(db)


async def _maybe_attach(db, kind: str, voucher: dict, raw: dict, user) -> dict:
    data_url = (raw or {}).get("attachment_data_url")
    if not data_url:
        return voucher
    filename = (raw or {}).get("attachment_filename") or "lampiran"
    meta = await attachment_svc.store_attachment(
        db,
        voucher_id=voucher["id"],
        voucher_kind=kind,
        user=user,
        filename=filename,
        data_url=data_url,
    )
    return await set_voucher_attachment(db, kind, voucher["id"], meta)


def register_cash_bank_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    # ── Master ──────────────────────────────────────────────
    @api_router.get("/cash-bank/accounts")
    async def get_cash_bank_accounts(
        q: Optional[str] = Query(None),
        active_only: bool = Query(False),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """List Kas/Bank. Zero-write."""
        db = get_db()
        items = await list_cash_banks(db, q=q, active_only=active_only)
        return {"items": items, "total": len(items)}

    @api_router.get("/cash-bank/accounts/{cash_bank_id}")
    async def get_cash_bank_account(
        cash_bank_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        doc = await get_cash_bank(db, cash_bank_id)
        if not doc:
            raise HTTPException(404, "Kas/Bank tidak ditemukan")
        return doc

    @api_router.post("/cash-bank/accounts", status_code=201)
    async def post_cash_bank_account(
        payload: CashBankCreatePayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await create_cash_bank(db, _dump(payload), user=user)
        except CashBankValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except CashBankConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/cash-bank/accounts/{cash_bank_id}")
    async def patch_cash_bank_account(
        cash_bank_id: str,
        payload: CashBankUpdatePayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            return await update_cash_bank(db, cash_bank_id, _dump(payload), user=user)
        except KeyError:
            raise HTTPException(404, "Kas/Bank tidak ditemukan") from None
        except CashBankValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except CashBankConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    # ── Penerimaan ──────────────────────────────────────────
    @api_router.get("/cash-bank/receipts")
    async def get_receipts(
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        items = await list_vouchers(db, KIND_RECEIPT, q=q)
        return {"items": items, "total": len(items)}

    @api_router.get("/cash-bank/receipts/{voucher_id}")
    async def get_receipt(
        voucher_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        doc = await get_voucher_with_journal(db, KIND_RECEIPT, voucher_id)
        if not doc:
            raise HTTPException(404, "Penerimaan tidak ditemukan")
        return doc

    @api_router.post("/cash-bank/receipts", status_code=201)
    async def post_receipt(
        payload: ReceiptPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        await _ensure_all(db)
        raw = _dump(payload)
        try:
            voucher = await create_receipt(db, raw, user=user)
            return await _maybe_attach(db, KIND_RECEIPT, voucher, raw, user)
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/cash-bank/receipts/{voucher_id}")
    async def patch_receipt(
        voucher_id: str,
        payload: ReceiptPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        raw = _dump(payload)
        try:
            voucher = await update_draft(db, KIND_RECEIPT, voucher_id, raw, user=user)
            return await _maybe_attach(db, KIND_RECEIPT, voucher, raw, user)
        except KeyError:
            raise HTTPException(404, "Penerimaan tidak ditemukan") from None
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/cash-bank/receipts/{voucher_id}")
    async def delete_receipt(
        voucher_id: str,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            deleted = await delete_draft(db, KIND_RECEIPT, voucher_id)
        except KeyError:
            raise HTTPException(404, "Penerimaan tidak ditemukan") from None
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "deleted": deleted}

    @api_router.post("/cash-bank/receipts/{voucher_id}/post")
    async def post_receipt_action(
        voucher_id: str,
        user=Depends(require_perm(PERM_POST)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await post_voucher(db, KIND_RECEIPT, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Penerimaan tidak ditemukan") from None
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/cash-bank/receipts/{voucher_id}/reverse")
    async def reverse_receipt_action(
        voucher_id: str,
        user=Depends(require_perm(PERM_REVERSE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await reverse_voucher(db, KIND_RECEIPT, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Penerimaan tidak ditemukan") from None
        except VoucherValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/cash-bank/receipts/{voucher_id}/attachment")
    async def upload_receipt_attachment(
        voucher_id: str,
        payload: AttachmentPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        voucher = await get_voucher(db, KIND_RECEIPT, voucher_id)
        if not voucher:
            raise HTTPException(404, "Penerimaan tidak ditemukan")
        meta = await attachment_svc.store_attachment(
            db,
            voucher_id=voucher_id,
            voucher_kind=KIND_RECEIPT,
            user=user,
            filename=payload.filename or "lampiran",
            data_url=payload.data_url,
        )
        return await set_voucher_attachment(db, KIND_RECEIPT, voucher_id, meta)

    @api_router.get("/cash-bank/receipts/{voucher_id}/attachment")
    async def download_receipt_attachment(
        voucher_id: str,
        download: bool = Query(False),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        voucher = await get_voucher(db, KIND_RECEIPT, voucher_id)
        if not voucher:
            raise HTTPException(404, "Penerimaan tidak ditemukan")
        data, ct, filename = await attachment_svc.load_attachment_bytes(
            db, voucher_id=voucher_id, file_id=(voucher.get("attachment") or {}).get("id") or ""
        )
        headers = {
            "Content-Disposition": attachment_svc.content_disposition(filename, download=download),
            "Cache-Control": "private, no-store",
        }
        return FastAPIResponse(content=data, media_type=ct, headers=headers)

    # ── Pembayaran ──────────────────────────────────────────
    @api_router.get("/cash-bank/payments")
    async def get_payments(
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        items = await list_vouchers(db, KIND_PAYMENT, q=q)
        return {"items": items, "total": len(items)}

    @api_router.get("/cash-bank/payments/{voucher_id}")
    async def get_payment(
        voucher_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        doc = await get_voucher_with_journal(db, KIND_PAYMENT, voucher_id)
        if not doc:
            raise HTTPException(404, "Pembayaran tidak ditemukan")
        return doc

    @api_router.post("/cash-bank/payments", status_code=201)
    async def post_payment(
        payload: PaymentPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        await _ensure_all(db)
        raw = _dump(payload)
        try:
            voucher = await create_payment(db, raw, user=user)
            return await _maybe_attach(db, KIND_PAYMENT, voucher, raw, user)
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/cash-bank/payments/{voucher_id}")
    async def patch_payment(
        voucher_id: str,
        payload: PaymentPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        raw = _dump(payload)
        try:
            voucher = await update_draft(db, KIND_PAYMENT, voucher_id, raw, user=user)
            return await _maybe_attach(db, KIND_PAYMENT, voucher, raw, user)
        except KeyError:
            raise HTTPException(404, "Pembayaran tidak ditemukan") from None
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/cash-bank/payments/{voucher_id}")
    async def delete_payment(
        voucher_id: str,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            deleted = await delete_draft(db, KIND_PAYMENT, voucher_id)
        except KeyError:
            raise HTTPException(404, "Pembayaran tidak ditemukan") from None
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "deleted": deleted}

    @api_router.post("/cash-bank/payments/{voucher_id}/post")
    async def post_payment_action(
        voucher_id: str,
        user=Depends(require_perm(PERM_POST)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await post_voucher(db, KIND_PAYMENT, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Pembayaran tidak ditemukan") from None
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/cash-bank/payments/{voucher_id}/reverse")
    async def reverse_payment_action(
        voucher_id: str,
        user=Depends(require_perm(PERM_REVERSE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await reverse_voucher(db, KIND_PAYMENT, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Pembayaran tidak ditemukan") from None
        except VoucherValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/cash-bank/payments/{voucher_id}/attachment")
    async def upload_payment_attachment(
        voucher_id: str,
        payload: AttachmentPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        voucher = await get_voucher(db, KIND_PAYMENT, voucher_id)
        if not voucher:
            raise HTTPException(404, "Pembayaran tidak ditemukan")
        meta = await attachment_svc.store_attachment(
            db,
            voucher_id=voucher_id,
            voucher_kind=KIND_PAYMENT,
            user=user,
            filename=payload.filename or "lampiran",
            data_url=payload.data_url,
        )
        return await set_voucher_attachment(db, KIND_PAYMENT, voucher_id, meta)

    @api_router.get("/cash-bank/payments/{voucher_id}/attachment")
    async def download_payment_attachment(
        voucher_id: str,
        download: bool = Query(False),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        voucher = await get_voucher(db, KIND_PAYMENT, voucher_id)
        if not voucher:
            raise HTTPException(404, "Pembayaran tidak ditemukan")
        data, ct, filename = await attachment_svc.load_attachment_bytes(
            db, voucher_id=voucher_id, file_id=(voucher.get("attachment") or {}).get("id") or ""
        )
        headers = {
            "Content-Disposition": attachment_svc.content_disposition(filename, download=download),
            "Cache-Control": "private, no-store",
        }
        return FastAPIResponse(content=data, media_type=ct, headers=headers)

    # ── Transfer ────────────────────────────────────────────
    @api_router.get("/cash-bank/transfers")
    async def get_transfers(
        q: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        items = await list_vouchers(db, KIND_TRANSFER, q=q)
        return {"items": items, "total": len(items)}

    @api_router.get("/cash-bank/transfers/{voucher_id}")
    async def get_transfer(
        voucher_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        doc = await get_voucher_with_journal(db, KIND_TRANSFER, voucher_id)
        if not doc:
            raise HTTPException(404, "Transfer tidak ditemukan")
        return doc

    @api_router.post("/cash-bank/transfers", status_code=201)
    async def post_transfer(
        payload: TransferPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await create_transfer(db, _dump(payload), user=user)
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/cash-bank/transfers/{voucher_id}")
    async def patch_transfer(
        voucher_id: str,
        payload: TransferPayload,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            return await update_draft(db, KIND_TRANSFER, voucher_id, _dump(payload), user=user)
        except KeyError:
            raise HTTPException(404, "Transfer tidak ditemukan") from None
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/cash-bank/transfers/{voucher_id}")
    async def delete_transfer(
        voucher_id: str,
        user=Depends(require_perm(PERM_MANAGE)),
    ):
        db = get_db()
        try:
            deleted = await delete_draft(db, KIND_TRANSFER, voucher_id)
        except KeyError:
            raise HTTPException(404, "Transfer tidak ditemukan") from None
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "deleted": deleted}

    @api_router.post("/cash-bank/transfers/{voucher_id}/post")
    async def post_transfer_action(
        voucher_id: str,
        user=Depends(require_perm(PERM_POST)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await post_voucher(db, KIND_TRANSFER, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Transfer tidak ditemukan") from None
        except (VoucherValidationError, CashBankValidationError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/cash-bank/transfers/{voucher_id}/reverse")
    async def reverse_transfer_action(
        voucher_id: str,
        user=Depends(require_perm(PERM_REVERSE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await reverse_voucher(db, KIND_TRANSFER, voucher_id, user=user)
        except KeyError:
            raise HTTPException(404, "Transfer tidak ditemukan") from None
        except VoucherValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except VoucherConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
