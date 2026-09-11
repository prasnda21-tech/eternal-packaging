"""Mongo indexes for minimal in-app notifications."""
from __future__ import annotations

from services.notifications import COLLECTION


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("id", unique=True)
    await db[COLLECTION].create_index("dedupe_key", unique=True)
    await db[COLLECTION].create_index([("user_id", 1), ("created_at", -1)])
