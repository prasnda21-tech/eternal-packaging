"""Read-only widget routes that are not already exposed with the right permission."""
from __future__ import annotations

from fastapi import HTTPException, Query

from services.cash_bank_master import COLLECTION as CASH_BANK_ACCOUNTS
from services.cash_bank_master import PERM_RECONCILE
from services.cash_bank_reconciliation import RECON_MATCHES, list_reconciliations
from services.purchase_period_summary import PERM_VIEW as PURCHASE_PERM
from services.purchase_period_summary import PurchasePeriodError, get_purchase_period_summary
from services.widget_profit_loss import PERM_VIEW as PROFIT_PERM
from services.widget_profit_loss import ProfitLossValidationError, get_widget_profit_loss


_PAIR_CAP = 200


async def _active_pair_summaries(db):
    """Active pair matches only. No amounts, journal ids, or statement line ids."""
    docs = await db[RECON_MATCHES].find(
        {"match_kind": "pair", "status": "active"},
        {"_id": 0, "id": 1, "cash_bank_id": 1, "date": 1, "direction": 1, "status": 1, "match_kind": 1},
    ).sort([("date", -1), ("id", 1)]).to_list(_PAIR_CAP + 1)
    truncated = len(docs) > _PAIR_CAP
    docs = docs[:_PAIR_CAP]
    ids = list({str(doc.get("cash_bank_id")) for doc in docs if doc.get("cash_bank_id")})
    names = {}
    if ids:
        accounts = await db[CASH_BANK_ACCOUNTS].find(
            {"id": {"$in": ids}},
            {"_id": 0, "id": 1, "name": 1},
        ).to_list(len(ids) + 5)
        names = {str(account.get("id")): account.get("name") for account in accounts if account.get("id")}
    rows = []
    for doc in docs:
        rows.append({
            "id": doc.get("id"),
            "match_kind": "pair",
            "status": "active",
            "cash_bank_name": names.get(str(doc.get("cash_bank_id") or "")) or "Rekening",
            "date": doc.get("date"),
            "direction": doc.get("direction") or "",
        })
    return rows, truncated


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
        """Legacy balance documents and active pair matches. Read only."""
        db = get_db()
        items = await list_reconciliations(db)
        pairs, pairs_truncated = await _active_pair_summaries(db)
        legacy_truncated = len(items) >= 2000
        if not items and not pairs:
            status = "empty"
            detail = "Belum ada pencocokan transaksi maupun rekonsiliasi saldo lama."
        elif legacy_truncated or pairs_truncated:
            status = "incomplete"
            detail = "Daftar mencapai batas baca. Total tidak diambil dari potongan ini."
        else:
            status = "complete"
            detail = None
        return {
            "status": status,
            "items": items,
            "pairs": pairs,
            "total": None if legacy_truncated else len(items),
            "pair_total": None if pairs_truncated else len(pairs),
            "shown": len(items),
            "pairs_shown": len(pairs),
            "truncated": legacy_truncated or pairs_truncated,
            "detail": detail,
        }
