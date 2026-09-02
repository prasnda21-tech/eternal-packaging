"""Assignment / reshuffle pool."""
from __future__ import annotations

from typing import Optional

from services.sales_activity.clock import iso_now
from services.sales_activity.constants import COL_OWNERSHIP, COL_OUTLETS, COL_POOL
from services.sales_activity.util import days_between, new_id, public_doc


async def enter_pool(db, snap: dict, *, reason: str, suggested_next_owner_id: Optional[str] = None) -> dict:
    existing = await db[COL_POOL].find_one({"outlet_id": snap["outlet_id"], "active": True}, {"_id": 0})
    if existing:
        return existing
    doc = {
        "id": new_id(),
        "outlet_id": snap["outlet_id"],
        "previous_owner_id": snap.get("current_owner_id"),
        "previous_owner_name": snap.get("current_owner_name"),
        "reason": reason,
        "entered_at": iso_now(),
        "left_at": None,
        "active": True,
        "suggested_next_owner_id": suggested_next_owner_id,
        "reassignment_count": int(snap.get("reassignment_count") or 0),
        "last_activity_at": snap.get("last_activity_at"),
        "next_follow_up_at": snap.get("next_follow_up_at"),
        "last_valid_sales_order_at": snap.get("last_valid_sales_order_at"),
        "conflict_state": snap.get("unresolved_reason"),
        "customer_kind": snap.get("customer_kind"),
    }
    await db[COL_POOL].insert_one(doc)
    return public_doc(doc)


async def leave_pool(db, outlet_id: str) -> None:
    await db[COL_POOL].update_many(
        {"outlet_id": outlet_id, "active": True},
        {"$set": {"active": False, "left_at": iso_now()}},
    )


async def list_pool(db, *, q: str = "", page: int = 1, page_size: int = 50) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    query = {"active": True}
    total = await db[COL_POOL].count_documents(query)
    rows = await db[COL_POOL].find(query, {"_id": 0}).sort("entered_at", 1).skip((page - 1) * page_size).limit(page_size).to_list(page_size)
    outlet_ids = [r["outlet_id"] for r in rows]
    outlets = {}
    if outlet_ids:
        async for o in db[COL_OUTLETS].find({"id": {"$in": outlet_ids}}, {"_id": 0}):
            outlets[o["id"]] = o
    ownerships = {}
    if outlet_ids:
        async for s in db[COL_OWNERSHIP].find({"outlet_id": {"$in": outlet_ids}}, {"_id": 0}):
            ownerships[s["outlet_id"]] = s
    needle = (q or "").strip().lower()
    items = []
    for row in rows:
        outlet = outlets.get(row["outlet_id"]) or {}
        if needle:
            hay = " ".join([
                str(outlet.get("name") or ""),
                str(row.get("previous_owner_name") or ""),
                str(row.get("reason") or ""),
            ]).lower()
            if needle not in hay:
                continue
        items.append({
            **public_doc(row),
            "outlet": outlet,
            "ownership": ownerships.get(row["outlet_id"]),
            "days_in_pool": days_between(row.get("entered_at")),
        })
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size),
    }
