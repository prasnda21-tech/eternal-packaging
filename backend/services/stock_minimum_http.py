"""HTTP routes for stock minimum settings and order warehouse allocations."""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import Body, HTTPException, Query
from pydantic import BaseModel, ConfigDict


class LoosePayload(BaseModel):
    model_config = ConfigDict(extra="allow")


def register_stock_minimum_routes(
    api_router,
    *,
    get_db,
    current_user,
    require_perm,
    require_any_perm,
    Depends,
):
    from services import stock_minimum as svc
    from services.branch_access import require_warehouse_access, resolve_stock_catalog_branch_id
    from services.stock_allocation import (
        attach_allocations_to_lines,
        load_allowed_warehouses_for_branch,
        summarize_order_allocation_warnings,
    )
    from services.min_stock_widget import build_min_stock_snapshot

    @api_router.get("/settings/stock-minimum")
    async def list_stock_minimums(
        branch_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        user=Depends(require_perm(svc.PERM_VIEW)),
    ):
        db = get_db()
        resolved = await resolve_stock_catalog_branch_id(db, user, branch_id)
        bid = (resolved.get("branch") or {}).get("id")
        if warehouse_id:
            await require_warehouse_access(db, user, warehouse_id, expected_branch_id=bid)
        items = await svc.list_minimums(db, branch_id=bid, warehouse_id=warehouse_id)
        return {"items": items, "branch_id": bid, "warehouse_id": warehouse_id}

    @api_router.put("/settings/stock-minimum")
    async def upsert_stock_minimum(payload: dict = Body(...), user=Depends(current_user)):
        await require_any_perm(user, svc.PERM_MANAGE)
        db = get_db()
        mid = str((payload or {}).get("master_item_id") or "").strip()
        bid = str((payload or {}).get("branch_id") or "").strip()
        wid = str((payload or {}).get("warehouse_id") or "").strip()
        if wid:
            await require_warehouse_access(db, user, wid, expected_branch_id=bid or None)
        clear = bool((payload or {}).get("clear"))
        result = await svc.upsert_minimum(
            db,
            master_item_id=mid,
            branch_id=bid,
            warehouse_id=wid,
            minimum_qty=(payload or {}).get("minimum_qty"),
            unit=str((payload or {}).get("unit") or "PCS"),
            user=user,
            clear=clear,
        )
        return result

    @api_router.get("/dashboard/widget/min-stock")
    async def widget_min_stock(
        branch_id: Optional[str] = None,
        warehouse_id: Optional[str] = None,
        page: int = Query(1),
        limit: int = Query(50),
        user=Depends(require_perm(svc.PERM_VIEW)),
    ):
        db = get_db()
        resolved = await resolve_stock_catalog_branch_id(db, user, branch_id)
        bid = (resolved.get("branch") or {}).get("id")
        if warehouse_id:
            await require_warehouse_access(db, user, warehouse_id, expected_branch_id=bid)
        snap = await build_min_stock_snapshot(
            db, branch_id=bid, warehouse_id=warehouse_id, page=page, limit=limit,
        )
        snap["effective_branch_id"] = bid
        return snap

    @api_router.patch("/orders/{order_id}/stock-allocations")
    async def patch_order_stock_allocations(
        order_id: str,
        payload: LoosePayload,
        user=Depends(current_user),
    ):
        """Update warehouse allocations on an existing SO. Never blocks on imbalance."""
        await require_any_perm(user, "sales_order.create", "po.edit")
        db = get_db()
        data = payload.model_dump() if payload else {}
        expected_updated = str(data.get("updated_at") or "").strip() or None
        order = await db.orders.find_one({"id": order_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, "PO tidak ditemukan")
        if order.get("cancelled"):
            raise HTTPException(400, "Pesanan sudah dibatalkan")
        from services.operational_branch import require_document_branch
        await require_document_branch(db, user, order)

        branch_id = str(order.get("operational_branch_id") or order.get("branch_id") or "")
        allowed = await load_allowed_warehouses_for_branch(db, branch_id)
        raw_by_line_id = data.get("line_allocations") if isinstance(data.get("line_allocations"), dict) else {}
        raw_by_index = None
        if isinstance(data.get("line_items"), list):
            raw_by_index = [
                (li.get("stock_allocations") if isinstance(li, dict) else None)
                for li in data.get("line_items") or []
            ]
        lines, warnings, _ = attach_allocations_to_lines(
            order.get("line_items") or [],
            branch_id=branch_id,
            allowed_warehouses=allowed,
            raw_by_line_id=raw_by_line_id,
            raw_by_index=raw_by_index,
        )
        summary = summarize_order_allocation_warnings(lines)
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        query: Dict[str, Any] = {"id": order_id, "cancelled": {"$ne": True}}
        if expected_updated:
            query["updated_at"] = expected_updated
        actor = {
            "id": str(user.get("id") or ""),
            "name": user.get("name") or user.get("email"),
            "email": user.get("email"),
        }
        result = await db.orders.update_one(
            query,
            {"$set": {
                "line_items": lines,
                "stock_allocation_summary": summary,
                "stock_allocations_updated_at": now,
                "stock_allocations_updated_by": actor,
                "updated_at": now,
            }},
        )
        if result.matched_count == 0:
            raise HTTPException(
                409,
                {
                    "code": "CONCURRENT_UPDATE",
                    "message": "Pesanan berubah di sesi lain. Muat ulang lalu simpan alokasi lagi.",
                },
            )
        fresh = await db.orders.find_one({"id": order_id}, {"_id": 0})
        return {
            "order": fresh,
            "warnings": warnings,
            "stock_allocation_summary": summary,
            "message": "Alokasi gudang disimpan",
        }
