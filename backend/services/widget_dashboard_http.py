"""Read-only widget routes that are not already exposed with the right permission."""
from __future__ import annotations

from fastapi import HTTPException, Query

from services.cash_bank_master import PERM_RECONCILE
from services.cash_bank_reconciliation import list_reconciliations
from services.purchase_period_summary import PERM_VIEW as PURCHASE_PERM
from services.purchase_period_summary import PurchasePeriodError, get_purchase_period_summary
from services.widget_profit_loss import PERM_VIEW as PROFIT_PERM
from services.widget_profit_loss import ProfitLossValidationError, get_widget_profit_loss


def register_widget_dashboard_routes(
    api_router,
    *,
    get_db,
    require_perm,
    Depends,
):
    @api_router.get("/dashboard/pembelian")
    async def get_dashboard_pembelian(
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        user=Depends(require_perm(PURCHASE_PERM)),
    ):
        """Period purchase invoice totals. purchase_invoices.view only."""
        db = get_db()
        try:
            return await get_purchase_period_summary(db, start_date=start_date, end_date=end_date)
        except PurchasePeriodError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/dashboard/widget/laba-rugi")
    async def get_dashboard_widget_profit(
        start_date: str = Query(..., min_length=1),
        end_date: str = Query(..., min_length=1),
        user=Depends(require_perm(PROFIT_PERM)),
    ):
        """Profit-loss components plus the existing incomplete blocker."""
        db = get_db()
        try:
            return await get_widget_profit_loss(db, start_date=start_date, end_date=end_date)
        except ProfitLossValidationError as exc:
            raise HTTPException(400, str(exc)) from exc

    @api_router.get("/dashboard/widget/reconciliations")
    async def get_dashboard_widget_reconciliations(
        user=Depends(require_perm(PERM_RECONCILE)),
    ):
        """Reconciliation documents. cash_bank.reconcile, not finance.view."""
        db = get_db()
        items = await list_reconciliations(db)
        truncated = len(items) >= 2000
        if not items:
            status = "empty"
            detail = "Belum ada dokumen rekonsiliasi."
        elif truncated:
            status = "incomplete"
            detail = "Daftar mencapai batas baca. Total tidak diambil dari potongan ini."
        else:
            status = "complete"
            detail = None
        return {
            "status": status,
            "items": items,
            "total": None if truncated else len(items),
            "shown": len(items),
            "truncated": truncated,
            "detail": detail,
        }
