"""Background poller for Urgent PO email outbox.

Compatible with existing Eternal uvicorn deployment (same pattern as
sales_activity evaluator). No separate production service / CI change.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Optional

logger = logging.getLogger("urgent_email_worker")

_task: Optional[asyncio.Task] = None


def worker_enabled() -> bool:
    """Worker loop runs when not explicitly disabled.

    SMTP may still be unconfigured — process_due_outbox parks jobs safely.
    Set URGENT_EMAIL_WORKER=0 to stop the loop entirely (tests).
    """
    flag = (os.environ.get("URGENT_EMAIL_WORKER") or "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _interval_sec() -> int:
    return max(5, int(os.environ.get("URGENT_EMAIL_WORKER_INTERVAL_SEC") or "15"))


async def _loop(db) -> None:
    from services.urgent_email_outbox import process_due_outbox

    while True:
        try:
            await process_due_outbox(db, limit=25)
        except Exception:
            logger.exception("urgent email worker loop failed")
        await asyncio.sleep(_interval_sec())


def start_urgent_email_worker(db) -> None:
    global _task
    if not worker_enabled():
        return
    if _task and not _task.done():
        return
    _task = asyncio.create_task(_loop(db), name="urgent-email-outbox-worker")
    logger.info("urgent email outbox worker started interval=%ss", _interval_sec())


def stop_urgent_email_worker() -> None:
    global _task
    if _task and not _task.done():
        _task.cancel()
    _task = None
