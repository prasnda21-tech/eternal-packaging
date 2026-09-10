"""Mongo indexes for Internal Work Chat."""
from __future__ import annotations

from services.chat_constants import COLL_ATTACHMENTS, COLL_MEMBERS, COLL_MESSAGES, COLL_ROOMS, ROOM_DM, ROOM_SYSTEM_GROUP, ROOM_WORK


async def ensure_indexes(db) -> None:
    await db[COLL_ROOMS].create_index(
        "dm_key",
        unique=True,
        partialFilterExpression={"type": ROOM_DM, "dm_key": {"$type": "string"}},
    )
    await db[COLL_ROOMS].create_index(
        "system_key",
        unique=True,
        partialFilterExpression={"type": ROOM_SYSTEM_GROUP, "system_key": {"$type": "string"}},
    )
    await db[COLL_ROOMS].create_index(
        "work_key",
        unique=True,
        partialFilterExpression={"type": ROOM_WORK, "work_key": {"$type": "string"}},
    )
    await db[COLL_ROOMS].create_index("last_activity_at")

    await db[COLL_MEMBERS].create_index([("room_id", 1), ("user_id", 1)], unique=True)
    await db[COLL_MEMBERS].create_index([("user_id", 1), ("active", 1)])

    await db[COLL_MESSAGES].create_index([("room_id", 1), ("sequence", 1)], unique=True)
    await db[COLL_MESSAGES].create_index(
        [("sender_user_id", 1), ("client_message_id", 1)],
        unique=True,
        partialFilterExpression={"client_message_id": {"$type": "string"}},
    )

    await db[COLL_ATTACHMENTS].create_index("attachment_id", unique=True)
    await db[COLL_ATTACHMENTS].create_index("id", unique=True)
    await db[COLL_ATTACHMENTS].create_index("room_id")
