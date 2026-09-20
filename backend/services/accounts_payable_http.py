"""HTTP routes for Buku Besar → Hutang Usaha. GET-only, zero-write."""
from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Query

from services.accounts_payable import (
    PERM_VIEW,
    AccountsPayableValidationError,
    get_accounts_payable_aging,
    get_accounts_payable_statement,
    get_accounts_payable_summary,
    get_accounts_payable_suppliers,
)


def register_accounts_payable_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/accounts-payable/summary")
    async def get_ledger_accounts_payable_summary(
        aging_reference_date: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        supplier_key: Optional[str] = Query(None),
        ap_account_id: Optional[str] = Query(None),
        aging_bucket: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_payable_summary(
                db,
                aging_reference_date=aging_reference_date,
                q=q,
                supplier_key=supplier_key,
                ap_account_id=ap_account_id,
                aging_bucket=aging_bucket,
            )
        except AccountsPayableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/ledger/accounts-payable/aging")
    async def get_ledger_accounts_payable_aging(
        aging_reference_date: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        supplier_key: Optional[str] = Query(None),
        ap_account_id: Optional[str] = Query(None),
        aging_bucket: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_payable_aging(
                db,
                aging_reference_date=aging_reference_date,
                q=q,
                supplier_key=supplier_key,
                ap_account_id=ap_account_id,
                aging_bucket=aging_bucket,
            )
        except AccountsPayableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/ledger/accounts-payable/suppliers")
    async def get_ledger_accounts_payable_suppliers(
        aging_reference_date: Optional[str] = Query(None),
        q: Optional[str] = Query(None),
        supplier_key: Optional[str] = Query(None),
        ap_account_id: Optional[str] = Query(None),
        aging_bucket: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_payable_suppliers(
                db,
                aging_reference_date=aging_reference_date,
                q=q,
                supplier_key=supplier_key,
                ap_account_id=ap_account_id,
                aging_bucket=aging_bucket,
            )
        except AccountsPayableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/ledger/accounts-payable/statement")
    async def get_ledger_accounts_payable_statement(
        supplier_key: Optional[str] = Query(None),
        start_date: Optional[str] = Query(None),
        end_date: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        db = get_db()
        try:
            return await get_accounts_payable_statement(
                db,
                supplier_key=supplier_key,
                start_date=start_date,
                end_date=end_date,
            )
        except AccountsPayableValidationError as exc:
            raise HTTPException(400, str(exc)) from exc
