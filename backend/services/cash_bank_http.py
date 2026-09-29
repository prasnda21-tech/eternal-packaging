"""HTTP routes for Kas & Bank Phase 1."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response as FastAPIResponse
from pydantic import BaseModel, ConfigDict, Field

from services import cash_bank_attachment as attachment_svc
from services.cash_bank_master import (
    PERM_MANAGE,
    PERM_POST,
    PERM_RECONCILE,
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
from services.cash_bank_mutasi import MutasiValidationError, get_mutasi
from services.cash_bank_reconciliation import (
    ReconConflictError,
    ReconValidationError,
    create_reconciliation,
    delete_draft_reconciliation,
    ensure_indexes as ensure_recon_indexes,
    finalize_reconciliation,
    get_reconciliation,
    get_reconciliation_workspace,
    list_reconciliations,
    list_unmatched_movements,
    set_matches,
    update_draft_reconciliation,
)
from services.cash_bank_statement import (
    StatementConflictError,
    StatementValidationError,
    commit_import,
    create_preview,
    ensure_indexes as ensure_statement_indexes,
    list_imports,
    resolve_duplicate,
    set_manual_balance,
    validate_preview,
)
from services.cash_bank_pair_match import (
    PairConflictError,
    PairValidationError,
    cancel_pair,
    create_pair,
    get_pair,
    list_legacy_reconciliations,
    pair_workspace,
    suggest_pairs,
)
from services.general_ledger import GeneralLedgerValidationError


class CashBankCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(..., min_length=1)
    account_id: str = Field(..., min_length=1)
    description: Optional[str] = ""
    active: bool = True
    currency_code: Optional[str] = None


class CashBankUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Optional[str] = None
    description: Optional[str] = None
    active: Optional[bool] = None
    account_id: Optional[str] = None
    currency_code: Optional[str] = None


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


class ReconciliationCreatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cash_bank_id: str = Field(..., min_length=1)
    reconciliation_date: str = Field(..., min_length=1)
    statement_balance: Any
    notes: Optional[str] = ""
    journal_line_ids: Optional[list] = None


class ReconciliationUpdatePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cash_bank_id: Optional[str] = None
    reconciliation_date: Optional[str] = None
    statement_balance: Optional[Any] = None
    notes: Optional[str] = None
    journal_line_ids: Optional[list] = None


class ReconciliationMatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    journal_line_ids: list = Field(default_factory=list)


class StatementCommitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preview_id: str = Field(..., min_length=1)
    mapping: dict


class StatementResolvePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1)


class ManualStatementBalancePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cash_bank_id: str = Field(..., min_length=1)
    as_of: str = Field(..., min_length=1)
    amount: Any


class PairMatchPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cash_bank_id: str = Field(..., min_length=1)
    statement_line_id: str = Field(..., min_length=1)
    journal_line_id: str = Field(..., min_length=1)
    idempotency_key: str = Field(..., min_length=1)
    start_date: str = Field(..., min_length=1)
    end_date: str = Field(..., min_length=1)


class PairCancelPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=1)


def _dump(payload) -> dict:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(exclude_unset=True)
    data = payload.dict(exclude_unset=True) if hasattr(payload, "dict") else payload.dict()
    return data

async def _ensure_all(db) -> None:
    await ensure_master_indexes(db)
    await ensure_voucher_indexes(db)
    await ensure_journal_indexes(db)
    await ensure_recon_indexes(db)
    await ensure_statement_indexes(db)


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

    # ── Mutasi (Phase 2) — GET zero-write ───────────────────
    @api_router.get("/cash-bank/mutasi")
    async def get_cash_bank_mutasi(
        cash_bank_id: str = Query(..., min_length=1),
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        q: Optional[str] = Query(None),
        source_type: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Mutasi / ledger from journal_lines. Zero-write."""
        db = get_db()
        try:
            return await get_mutasi(
                db,
                cash_bank_id=cash_bank_id,
                start_date=start_date,
                end_date=end_date,
                q=q,
                source_type=source_type,
            )
        except MutasiValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ── Rekonsiliasi (Phase 2) ──────────────────────────────
    @api_router.get("/cash-bank/reconciliations")
    async def get_reconciliations(
        cash_bank_id: Optional[str] = Query(None),
        status: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """List reconciliations. Zero-write."""
        db = get_db()
        items = await list_reconciliations(db, cash_bank_id=cash_bank_id, status=status)
        return {"items": items, "total": len(items)}

    @api_router.get("/cash-bank/reconciliations/workspace")
    async def get_recon_workspace(
        cash_bank_id: str = Query(..., min_length=1),
        reconciliation_date: str = Query(..., min_length=1),
        reconciliation_id: Optional[str] = Query(None),
        statement_balance: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Preview unmatched movements + book balance. Zero-write — no auto-create."""
        db = get_db()
        try:
            return await get_reconciliation_workspace(
                db,
                cash_bank_id=cash_bank_id,
                reconciliation_date=reconciliation_date,
                reconciliation_id=reconciliation_id,
                statement_balance=statement_balance,
            )
        except ReconValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.post("/cash-bank/statements/preview", status_code=201)
    async def post_statement_preview(
        cash_bank_id: str = Form(...),
        file: UploadFile = File(...),
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        await _ensure_all(db)
        data = await file.read()
        try:
            return await create_preview(
                db,
                cash_bank_id=cash_bank_id,
                filename=file.filename or "mutasi.csv",
                data=data,
                user=user,
            )
        except StatementValidationError as exc:
            if exc.errors:
                raise HTTPException(400, {"message": str(exc), "errors": exc.errors}) from exc
            raise HTTPException(400, str(exc)) from exc

    @api_router.post("/cash-bank/statements/preview/{preview_id}/validate")
    async def post_statement_validate(
        preview_id: str,
        payload: StatementCommitPayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        if payload.preview_id != preview_id:
            raise HTTPException(400, "Pratinjau tidak sesuai")
        try:
            return await validate_preview(db, preview_id, payload.mapping)
        except StatementValidationError as exc:
            raise HTTPException(400, {"message": str(exc), "errors": exc.errors}) from exc
        except StatementConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except CashBankValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.post("/cash-bank/statements/imports", status_code=201)
    async def post_statement_import(
        payload: StatementCommitPayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await commit_import(
                db, preview_id=payload.preview_id, mapping_raw=payload.mapping, user=user
            )
        except StatementValidationError as exc:
            raise HTTPException(400, {"message": str(exc), "errors": exc.errors}) from exc
        except StatementConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        except CashBankValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/cash-bank/statements/imports")
    async def get_statement_imports(
        cash_bank_id: str = Query(..., min_length=1),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        items = await list_imports(db, cash_bank_id=cash_bank_id)
        return {"items": items, "total": len(items)}

    @api_router.post("/cash-bank/statements/lines/{line_id}/duplicate-resolution")
    async def post_statement_duplicate_resolution(
        line_id: str,
        payload: StatementResolvePayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        try:
            row = await resolve_duplicate(
                db,
                line_id,
                action=payload.action,
                reason=payload.reason,
                idempotency_key=payload.idempotency_key,
                user=user,
            )
        except KeyError:
            raise HTTPException(404, "Baris rekening koran tidak ditemukan") from None
        except StatementValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except StatementConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {
            "id": row.get("id"),
            "duplicate_status": row.get("duplicate_status"),
            "resolutions": row.get("resolutions") or [],
        }

    @api_router.put("/cash-bank/statements/manual-balance")
    async def put_manual_statement_balance(
        payload: ManualStatementBalancePayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await set_manual_balance(
                db,
                cash_bank_id=payload.cash_bank_id,
                as_of=payload.as_of,
                amount=payload.amount,
                user=user,
            )
        except StatementValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/cash-bank/reconciliations/pairs/workspace")
    async def get_pair_workspace(
        cash_bank_id: str = Query(..., min_length=1),
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        q: Optional[str] = Query(None),
        page: int = Query(1, ge=1),
        page_size: int = Query(50, ge=1, le=100),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await pair_workspace(
                db,
                cash_bank_id=cash_bank_id,
                start=start_date,
                end=end_date,
                q=q,
                page=page,
                page_size=page_size,
            )
        except PairValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except GeneralLedgerValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/cash-bank/reconciliations/pairs/suggestions")
    async def get_pair_suggestions(
        statement_line_id: str = Query(..., min_length=1),
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await suggest_pairs(
                db,
                statement_line_id=statement_line_id,
                start=start_date,
                end=end_date,
            )
        except PairValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/cash-bank/reconciliations/legacy")
    async def get_legacy_reconciliations(
        cash_bank_id: str = Query(..., min_length=1),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        items = await list_legacy_reconciliations(db, cash_bank_id)
        return {"items": items, "total": len(items)}

    @api_router.get("/cash-bank/reconciliations/pairs/{match_id}")
    async def get_pair_match(
        match_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_pair(db, match_id)
        except KeyError:
            raise HTTPException(404, "Pencocokan tidak ditemukan") from None

    @api_router.post("/cash-bank/reconciliations/pairs", status_code=201)
    async def post_pair_match(
        payload: PairMatchPayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await create_pair(
                db,
                cash_bank_id=payload.cash_bank_id,
                statement_line_id=payload.statement_line_id,
                journal_line_id=payload.journal_line_id,
                idempotency_key=payload.idempotency_key,
                start=payload.start_date,
                end=payload.end_date,
                user=user,
            )
        except PairValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PairConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.post("/cash-bank/reconciliations/pairs/{match_id}/cancel")
    async def post_pair_cancel(
        match_id: str,
        payload: PairCancelPayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        try:
            return await cancel_pair(
                db,
                match_id,
                idempotency_key=payload.idempotency_key,
                reason=payload.reason,
                user=user,
            )
        except KeyError:
            raise HTTPException(404, "Pencocokan tidak ditemukan") from None
        except PairValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except PairConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.get("/cash-bank/reconciliations/{recon_id}")
    async def get_reconciliation_detail(
        recon_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Reconciliation detail. Zero-write."""
        db = get_db()
        doc = await get_reconciliation(db, recon_id)
        if not doc:
            raise HTTPException(404, "Rekonsiliasi tidak ditemukan")
        return doc

    @api_router.get("/cash-bank/reconciliations/{recon_id}/unmatched")
    async def get_recon_unmatched(
        recon_id: str,
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Unmatched (+ own matched) movements for a reconciliation. Zero-write."""
        db = get_db()
        doc = await get_reconciliation(db, recon_id, with_matches=False)
        if not doc:
            raise HTTPException(404, "Rekonsiliasi tidak ditemukan")
        try:
            items = await list_unmatched_movements(
                db,
                cash_bank_id=doc["cash_bank_id"],
                through_date=doc["reconciliation_date"],
                reconciliation_id=recon_id,
            )
        except ReconValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"items": items, "total": len(items)}

    @api_router.post("/cash-bank/reconciliations", status_code=201)
    async def post_reconciliation(
        payload: ReconciliationCreatePayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await create_reconciliation(db, _dump(payload), user=user)
        except ReconValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except CashBankValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ReconConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.patch("/cash-bank/reconciliations/{recon_id}")
    async def patch_reconciliation(
        recon_id: str,
        payload: ReconciliationUpdatePayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        try:
            return await update_draft_reconciliation(db, recon_id, _dump(payload), user=user)
        except KeyError:
            raise HTTPException(404, "Rekonsiliasi tidak ditemukan") from None
        except ReconValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except CashBankValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ReconConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.put("/cash-bank/reconciliations/{recon_id}/matches")
    async def put_reconciliation_matches(
        recon_id: str,
        payload: ReconciliationMatchPayload,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        try:
            return await set_matches(
                db, recon_id, _dump(payload).get("journal_line_ids") or [], user=user
            )
        except KeyError:
            raise HTTPException(404, "Rekonsiliasi tidak ditemukan") from None
        except ReconValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ReconConflictError as exc:
            raise HTTPException(409, str(exc)) from exc

    @api_router.delete("/cash-bank/reconciliations/{recon_id}")
    async def delete_reconciliation(
        recon_id: str,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        try:
            deleted = await delete_draft_reconciliation(db, recon_id)
        except KeyError:
            raise HTTPException(404, "Rekonsiliasi tidak ditemukan") from None
        except ReconConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "deleted": deleted}

    @api_router.post("/cash-bank/reconciliations/{recon_id}/finalize")
    async def post_finalize_reconciliation(
        recon_id: str,
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        db = get_db()
        await _ensure_all(db)
        try:
            return await finalize_reconciliation(db, recon_id, user=user)
        except KeyError:
            raise HTTPException(404, "Rekonsiliasi tidak ditemukan") from None
        except ReconValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
        except ReconConflictError as exc:
            raise HTTPException(409, str(exc)) from exc
