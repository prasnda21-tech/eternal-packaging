"""Additive indexes for Sales Activity collections and last-order lookups."""
from __future__ import annotations

from services.sales_activity.constants import (
    COL_ACTIVITIES,
    COL_BATCHES,
    COL_CONFLICTS,
    COL_EVENTS,
    COL_OWNERSHIP,
    COL_OUTLETS,
    COL_POOL,
    COL_VISIT_PHOTOS,
)


async def _partial_unique_string(coll, field: str) -> None:
    try:
        await coll.drop_index(f"{field}_1")
    except Exception:
        pass
    await coll.create_index(
        field,
        unique=True,
        partialFilterExpression={field: {"$type": "string"}},
    )


async def ensure_indexes(db) -> None:
    await db[COL_OUTLETS].create_index("id", unique=True)
    await _partial_unique_string(db[COL_OUTLETS], "source_identity")
    await db[COL_OUTLETS].create_index("import_batch_id")
    await db[COL_OUTLETS].create_index("identity_keys.outlet_code", sparse=True)
    await db[COL_OUTLETS].create_index("identity_keys.whatsapp_norm", sparse=True)
    await db[COL_OUTLETS].create_index("name")

    await db[COL_OWNERSHIP].create_index("id", unique=True)
    await db[COL_OWNERSHIP].create_index("outlet_id", unique=True)
    await db[COL_OWNERSHIP].create_index([("automation_status", 1), ("in_pool", 1), ("eligible_at", 1)])
    await db[COL_OWNERSHIP].create_index("current_owner_id")

    await db[COL_EVENTS].create_index("event_id", unique=True)
    await db[COL_EVENTS].create_index("correlation_key", unique=True)
    await db[COL_EVENTS].create_index([("outlet_id", 1), ("occurred_at", 1)])

    await db[COL_ACTIVITIES].create_index("id", unique=True)
    await _partial_unique_string(db[COL_ACTIVITIES], "source_identity")
    await db[COL_ACTIVITIES].create_index([("outlet_id", 1), ("activity_at", -1)])
    await db[COL_ACTIVITIES].create_index("sales_user_id")

    await db[COL_VISIT_PHOTOS].create_index("id", unique=True)
    await db[COL_VISIT_PHOTOS].create_index("activity_id")
    await db[COL_VISIT_PHOTOS].create_index("outlet_id")
    await db[COL_VISIT_PHOTOS].create_index([("activity_id", 1), ("sort_order", 1)])

    await db[COL_POOL].create_index("id", unique=True)
    await db[COL_POOL].create_index([("outlet_id", 1), ("active", 1)])
    await db[COL_POOL].create_index("entered_at")

    await db[COL_BATCHES].create_index("id", unique=True)
    await db[COL_BATCHES].create_index("file_hash", unique=True)
    await db[COL_BATCHES].create_index("created_at")

    await db[COL_CONFLICTS].create_index("id", unique=True)
    await db[COL_CONFLICTS].create_index("import_batch_id")
    await db[COL_CONFLICTS].create_index("source_identity", unique=True, sparse=True)

    await db.orders.create_index([("cancelled", 1), ("customer", 1), ("order_date", -1)], name="sa_orders_customer_date")
    await db.orders.create_index(
        [("cancelled", 1), ("customer_id", 1), ("order_date", -1)],
        name="sa_orders_customer_id_date",
        sparse=True,
    )
