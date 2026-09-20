"""HTTP routes for Buku Besar → Piutang Usaha. GET-only, zero-write."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query

from services.accounts_receivable import (
    PERM_VIEW,
    AccountsReceivableValidationError,
    get_accounts_receivable_aging,
    get_accounts_receivable_customers,
    get_accounts_receivable_statement,
    get_accounts_receivable_summary,
)


def register_accounts_receivable_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/accounts-receivable/summary")
    async def get_ledger_accounts_receivable_summary(
        aging_reference_date: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        customer_key: Optional[str] = Query(None),
        sales: Optional[str] = Query(None),
        ar_account_id: Optional[str] = Query(None),
        aging_bucket: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_receivable_summary(
                db,
                aging_reference_date=aging_reference_date,
                q=q,
                customer_key=customer_key,
                sales=sales,
                ar_account_id=ar_account_id,
                aging_bucket=aging_bucket,
            )
        except AccountsReceivableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/ledger/accounts-receivable/aging")
    async def get_ledger_accounts_receivable_aging(
        aging_reference_date: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        customer_key: Optional[str] = Query(None),
        sales: Optional[str] = Query(None),
        ar_account_id: Optional[str] = Query(None),
        aging_bucket: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_receivable_aging(
                db,
                aging_reference_date=aging_reference_date,
                q=q,
                customer_key=customer_key,
                sales=sales,
                ar_account_id=ar_account_id,
                aging_bucket=aging_bucket,
            )
        except AccountsReceivableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/ledger/accounts-receivable/customers")
    async def get_ledger_accounts_receivable_customers(
        aging_reference_date: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        customer_key: Optional[str] = Query(None),
        sales: Optional[str] = Query(None),
        ar_account_id: Optional[str] = Query(None),
        aging_bucket: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_receivable_customers(
                db,
                aging_reference_date=aging_reference_date,
                q=q,
                customer_key=customer_key,
                sales=sales,
                ar_account_id=ar_account_id,
                aging_bucket=aging_bucket,
            )
        except AccountsReceivableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/ledger/accounts-receivable/statement")
    async def get_ledger_accounts_receivable_statement(
        customer_key: Optional[str] = Query(None),
        start_date: Optional[str] = Query(None),
        end_date: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_receivable_statement(
                db,
                customer_key=customer_key,
                start_date=start_date,
                end_date=end_date,
            )
        except AccountsReceivableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
