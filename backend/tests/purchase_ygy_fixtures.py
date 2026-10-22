"""Shared YGY branch/warehouse seed for purchasing tests under PUR-LOC-01 ygy_first.

Does not create production fixtures; upserts into the isolated test DB only.
Avoids creating a second active YGY warehouse (resolve_purchase_receipt_warehouse
requires exactly one when warehouse_id is omitted).
"""
from __future__ import annotations

YGY_BRANCH_ID = "branch-ygy"
YGY_WAREHOUSE_ID = "wh-ygy"


async def ensure_active_ygy(db) -> dict:
    """Upsert active YGY; ensure exactly one active warehouse on that branch."""
    await db.branches.update_one(
        {"id": YGY_BRANCH_ID},
        {
            "$set": {
                "id": YGY_BRANCH_ID,
                "code": "YGY",
                "name": "Yogyakarta",
                "active": True,
            }
        },
        upsert=True,
    )
    active = await db.warehouses.find(
        {"branch_id": YGY_BRANCH_ID, "active": True},
        {"_id": 0, "id": 1},
    ).to_list(20)
    if len(active) == 1 and active[0].get("id"):
        return {"branch_id": YGY_BRANCH_ID, "warehouse_id": str(active[0]["id"])}
    if len(active) > 1:
        keep = str(active[0]["id"])
        for row in active[1:]:
            await db.warehouses.update_one(
                {"id": row["id"]},
                {"$set": {"active": False}},
            )
        return {"branch_id": YGY_BRANCH_ID, "warehouse_id": keep}
    await db.warehouses.update_one(
        {"id": YGY_WAREHOUSE_ID},
        {
            "$set": {
                "id": YGY_WAREHOUSE_ID,
                "branch_id": YGY_BRANCH_ID,
                "code": "YGY-UTAMA",
                "name": "Gudang YGY",
                "active": True,
            }
        },
        upsert=True,
    )
    return {"branch_id": YGY_BRANCH_ID, "warehouse_id": YGY_WAREHOUSE_ID}
