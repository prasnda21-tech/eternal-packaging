"""HTTP routes for financial inventory-issue (COGS) queue and posting."""
from __future__ import annotations

from fastapi import HTTPException

from services.inventory_issue_accounting import (
    evaluate_inventory_issue,
    evaluate_inventory_issues,
    public_issue_view,
)
from services.inventory_issue_posting import PERM_POST, PERM_VIEW, post_inventory_issue_accounting


def register_inventory_issue_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    Depends,
):
    @api_router.get("/ledger/inventory-issues")
    async def list_inventory_issues(user=Depends(require_perm(PERM_VIEW))):
        """Read-only financial issue queue. Zero-write."""
        db = get_db()
        rows = await evaluate_inventory_issues(db)
        return {"items": [public_issue_view(row) for row in rows]}

    @api_router.get("/ledger/inventory-issues/{movement_id}")
    async def get_inventory_issue(movement_id: str, user=Depends(require_perm(PERM_VIEW))):
        """Read-only financial issue detail. Zero-write."""
        db = get_db()
        row = await evaluate_inventory_issue(db, movement_id)
        if not row:
            raise HTTPException(404, f"Mutasi '{movement_id}' tidak ditemukan.")
        return {"item": public_issue_view(row)}

    @api_router.post("/ledger/inventory-issues/{movement_id}/post")
    async def post_inventory_issue(movement_id: str, user=Depends(require_perm(PERM_POST))):
        db = get_db()
        item = await post_inventory_issue_accounting(db, movement_id, user)
        return {"item": item}
