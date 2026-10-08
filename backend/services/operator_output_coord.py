"""Durable per-source_key coordination for Gaji Operator repair vs wage claims.

Mongo multi-document transactions require a replica set. Local/dev often runs
standalone, so this module uses a unique fence document per source_key instead.

Both progress_entry repairs and HELD wage-claim creation must hold the same
fence for a source_key before mutating. Crash recovery: expired leases may be
taken over via compare-and-set on holder+expires_at.

Stale-holder protection: writers must call require_coord_holder() immediately
before each mutating write. That call only succeeds when this holder still owns
the fence, and it renews the lease. A takeover after expiry makes the original
holder fail before it can commit. Heartbeat renewals keep long-lived holders
alive without lengthening LEASE_SECONDS.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator, Optional

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

COORD_LOCKS = "hr_operator_output_coord_locks"
COORD_INDEX = "uniq_output_coord_source"
LEASE_SECONDS = 45
HEARTBEAT_SECONDS = 15.0  # renew while holding; must stay < LEASE_SECONDS
DEFAULT_WAIT_SECONDS = 15.0
LOST_LOCK_DETAIL = (
    "Kunci koordinasi output hilang atau diambil alih proses lain. "
    "Muat ulang lalu coba lagi."
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _parse_iso(raw: Optional[str]) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _expired(doc: Optional[dict], *, now: Optional[datetime] = None) -> bool:
    if not doc:
        return True
    expires = _parse_iso(doc.get("expires_at"))
    if expires is None:
        # Missing/invalid lease timestamp → recoverable (treat as stale).
        return True
    current = now or _now()
    return expires <= current


async def ensure_coord_indexes(db) -> None:
    await db[COORD_LOCKS].create_index("source_key", unique=True, name=COORD_INDEX)


async def require_coord_holder(db, source_key: str, holder: str) -> None:
    """Renew lease only if this holder still owns the fence; else 409.

    Call immediately before every mutating write so a takeover after expiry
    cannot be followed by a stale writer commit.
    """
    key = str(source_key or "").strip()
    token = str(holder or "").strip()
    if not key or not token:
        raise HTTPException(409, LOST_LOCK_DETAIL)
    now = _now()
    updated = await db[COORD_LOCKS].find_one_and_update(
        {"source_key": key, "holder": token},
        {"$set": {
            "expires_at": _iso(now + timedelta(seconds=LEASE_SECONDS)),
            "refreshed_at": _iso(now),
        }},
        return_document=ReturnDocument.AFTER,
    )
    if isinstance(updated, dict) and str(updated.get("holder") or "") == token:
        return
    raise HTTPException(409, LOST_LOCK_DETAIL)


async def _takeover_expired(db, source_key: str, current: dict, holder: str, purpose: str) -> bool:
    if not _expired(current):
        return False
    now = _now()
    updated = await db[COORD_LOCKS].find_one_and_update(
        {
            "source_key": source_key,
            "holder": current.get("holder"),
            "expires_at": current.get("expires_at"),
        },
        {"$set": {
            "holder": holder,
            "purpose": purpose,
            "acquired_at": _iso(now),
            "expires_at": _iso(now + timedelta(seconds=LEASE_SECONDS)),
            "recovered_from": current.get("holder") or "",
            "generation": int(current.get("generation") or 0) + 1,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if isinstance(updated, dict):
        return str(updated.get("holder") or "") == holder
    row = await db[COORD_LOCKS].find_one({"source_key": source_key, "holder": holder})
    return bool(row)


@asynccontextmanager
async def coordinate_source_key(
    db,
    source_key: str,
    *,
    purpose: str,
    wait_seconds: float = DEFAULT_WAIT_SECONDS,
) -> AsyncIterator[str]:
    """Exclusive durable fence for one source_key. Crash-safe via lease expiry."""
    key = str(source_key or "").strip()
    if not key:
        raise HTTPException(400, "source_key wajib untuk koordinasi payroll.")
    holder = str(uuid.uuid4())
    deadline = time.monotonic() + max(0.5, float(wait_seconds))
    acquired = False

    while not acquired:
        now = _now()
        try:
            await db[COORD_LOCKS].insert_one({
                "source_key": key,
                "holder": holder,
                "purpose": str(purpose or ""),
                "acquired_at": _iso(now),
                "expires_at": _iso(now + timedelta(seconds=LEASE_SECONDS)),
                "generation": 0,
            })
            acquired = True
            break
        except DuplicateKeyError:
            current = await db[COORD_LOCKS].find_one({"source_key": key})
            if current and await _takeover_expired(db, key, current, holder, purpose):
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise HTTPException(
                    409,
                    "Output sedang dikunci oleh proses payroll/koreksi lain. Coba lagi sebentar.",
                )
            await asyncio.sleep(0.02)

    stop = asyncio.Event()

    async def _heartbeat() -> None:
        # Keep live holders from being taken over during long claim batches.
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
                return
            except asyncio.TimeoutError:
                try:
                    await require_coord_holder(db, key, holder)
                except HTTPException:
                    # Ownership lost; next require_coord_holder before write will 409.
                    return

    beat = asyncio.create_task(_heartbeat())
    try:
        yield holder
    finally:
        stop.set()
        beat.cancel()
        try:
            await beat
        except asyncio.CancelledError:
            pass
        await db[COORD_LOCKS].delete_one({"source_key": key, "holder": holder})
