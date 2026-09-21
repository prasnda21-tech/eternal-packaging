"""HTTP routes for Inventory / COGS reconciliation (Phase 5B.1)."""
from __future__ import annotations

from typing import Optional

from fastapi import Query

from services.inventory_reconciliation import (
    PERM_VIEW,
    get_inventory_reconciliation_accounts,
    get_inventory_reconciliation_items,
    get_inventory_reconciliation_summary,
)


def register_inventory_reconciliation_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/inventory-reconciliation/summary")
    async def get_ledger_inventory_reconciliation_summary(
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Current recorded Inventory / COGS reconciliation. Zero-write."""
        db = get_db()
        return await get_inventory_reconciliation_summary(db)

    @api_router.get("/ledger/inventory-reconciliation/accounts")
    async def get_ledger_inventory_reconciliation_accounts(
        account_id: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Per-account Inventory / COGS reconciliation. Filter does not change totals."""
        db = get_db()
        return await get_inventory_reconciliation_accounts(db, account_id=account_id)

    @api_router.get("/ledger/inventory-reconciliation/items")
    async def get_ledger_inventory_reconciliation_items(
        q: Optional[str] = Query(None),
        item: Optional[str] = Query(None),
        user=Depends(require_perm(PERM_VIEW)),
    ):
        """Per-item physical / financial quantity comparison. Filter is presentation only."""
        db = get_db()
        return await get_inventory_reconciliation_items(db, q=q, item=item)
