"""Monthly accounting periods + lease-based writer claims.

Missing row = OPEN. No historical backfill.
Writer claims (token + expires_at) are the close/post mutex.
Expired claims do not block close. Release is token-scoped only.
"""
from __future__ import annotations

import calendar
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo.errors import DuplicateKeyError

from services.accounting_audit import (
    COLLECTION as AUDIT_COLLECTION,
    ENTITY_PERIOD,
    EVENT_PERIOD_CLOSED,
    EVENT_PERIOD_REOPENED,
    record_accounting_event,
)
from services.purchase_common import actor_snapshot, now_iso

COLLECTION = "accounting_periods"

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

WRITER_CLAIM_TTL_SECONDS = 600

PERM_VIEW = "accounting.period.view"
PERM_CLOSE = "accounting.period.close"
PERM_REOPEN = "accounting.period.reopen"

PERIOD_CLOSED_MESSAGE = "Periode akuntansi {period_key} sudah ditutup."
PERIOD_WRITERS_MESSAGE = "Periode sedang diposting. Coba tutup lagi."
CLAIM_EXPIRED_MESSAGE = "Klaim periode berakhir. Coba posting ulang."
PERIOD_RECOVERY_MESSAGE = (
    "Periode belum dapat ditutup karena terdapat recovery akuntansi yang belum selesai."
)
PERIOD_REASON_MIN_LENGTH = 3
PERIOD_REASON_MESSAGE = "Alasan wajib diisi (minimal 3 karakter)."
OB_RECOVERY_COLLECTION = "opening_balance_vouchers"


class PeriodLockedError(Exception):
    """New journal insert/reversal blocked because the period is closed."""


class PeriodConflictError(Exception):
    """Close/reopen cannot proceed (writers in flight, already closed, etc.)."""


class PeriodValidationError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def period_key_from_date(date: str) -> str:
    raw = str(date or "").strip()
    if len(raw) >= 7 and raw[4] == "-" and raw[5:7].isdigit():
        year = raw[:4]
        month = raw[5:7]
        if year.isdigit() and 1 <= int(month) <= 12:
            return f"{year}-{month}"
    raise PeriodValidationError("Tanggal jurnal tidak valid untuk periode akuntansi")


def month_bounds(period_key: str) -> Tuple[str, str]:
    key = normalize_period_key(period_key)
    year = int(key[:4])
    month = int(key[5:7])
    last = calendar.monthrange(year, month)[1]
    return f"{key}-01", f"{key}-{last:02d}"


def normalize_period_key(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) >= 7 and text[4] == "-":
        key = text[:7]
        year, month = key.split("-", 1)
        if year.isdigit() and month.isdigit() and 1 <= int(month) <= 12:
            return f"{int(year):04d}-{int(month):02d}"
    raise PeriodValidationError("period_key harus YYYY-MM")


def _fresh_claim_elem(now: Optional[str] = None) -> Dict[str, Any]:
    return {"expires_at": {"$gt": now or now_iso()}}


def _has_fresh_claims(doc: Optional[dict], *, now: Optional[str] = None) -> bool:
    stamp = now or now_iso()
    for claim in (doc or {}).get("writer_claims") or []:
        if str(claim.get("expires_at") or "") > stamp:
            return True
    return False


def _fresh_claim_count(doc: Optional[dict], *, now: Optional[str] = None) -> int:
    stamp = now or now_iso()
    return sum(
        1
        for claim in (doc or {}).get("writer_claims") or []
        if str(claim.get("expires_at") or "") > stamp
    )


def public_period(doc: Optional[dict], *, period_key: Optional[str] = None) -> dict:
    key = (doc or {}).get("period_key") or period_key
    if key:
        try:
            start, end = month_bounds(str(key))
        except PeriodValidationError:
            start, end = None, None
    else:
        start, end = None, None
    status = str((doc or {}).get("status") or STATUS_OPEN)
    if status not in (STATUS_OPEN, STATUS_CLOSED):
        status = STATUS_OPEN
    if not doc:
        status = STATUS_OPEN
    return {
        "id": (doc or {}).get("id"),
        "period_key": str(key or ""),
        "start_date": (doc or {}).get("start_date") or start,
        "end_date": (doc or {}).get("end_date") or end,
        "status": status,
        "closed_at": (doc or {}).get("closed_at"),
        "closed_by": (doc or {}).get("closed_by"),
        "close_reason": (doc or {}).get("close_reason") or "",
        "reopened_at": (doc or {}).get("reopened_at"),
        "reopened_by": (doc or {}).get("reopened_by"),
        "reopen_reason": (doc or {}).get("reopen_reason") or "",
        "created_at": (doc or {}).get("created_at"),
        "updated_at": (doc or {}).get("updated_at"),
        "has_fresh_writer_claims": _has_fresh_claims(doc),
        "fresh_writer_claim_count": _fresh_claim_count(doc),
    }


def _closed_message(period_key: str) -> str:
    return PERIOD_CLOSED_MESSAGE.format(period_key=period_key)


def normalize_period_reason(raw: Any) -> str:
    text = str(raw or "").strip()
    if len(text) < PERIOD_REASON_MIN_LENGTH:
        raise PeriodValidationError(PERIOD_REASON_MESSAGE)
    return text


def period_close_operation_key(period_key: str, closed_at: Any) -> str:
    return f"period:{period_key}:close:{closed_at}"


def period_reopen_operation_key(period_key: str, reopened_at: Any) -> str:
    return f"period:{period_key}:reopen:{reopened_at}"


def _new_period_doc(period_key: str, *, claims: Optional[List[dict]] = None) -> dict:
    start, end = month_bounds(period_key)
    now = now_iso()
    return {
        "id": str(uuid.uuid4()),
        "period_key": period_key,
        "start_date": start,
        "end_date": end,
        "status": STATUS_OPEN,
        "writer_claims": list(claims or []),
        "closed_at": None,
        "closed_by": None,
        "close_reason": "",
        "reopened_at": None,
        "reopened_by": None,
        "reopen_reason": "",
        "created_at": now,
        "updated_at": now,
    }


async def ensure_indexes(db) -> None:
    col = db[COLLECTION]
    await col.create_index("id", unique=True)
    await col.create_index("period_key", unique=True)
    await col.create_index("status")


async def get_period_raw(db, period_key: str) -> Optional[dict]:
    key = normalize_period_key(period_key)
    return await db[COLLECTION].find_one({"period_key": key}, {"_id": 0})


async def get_period(db, period_key: str) -> dict:
    key = normalize_period_key(period_key)
    doc = await get_period_raw(db, key)
    return public_period(doc, period_key=key)


def _iter_month_keys(start_key: str, end_key: str) -> List[str]:
    start = normalize_period_key(start_key)
    end = normalize_period_key(end_key)
    if start > end:
        start, end = end, start
    year, month = int(start[:4]), int(start[5:7])
    end_year, end_month = int(end[:4]), int(end[5:7])
    out: List[str] = []
    while (year, month) <= (end_year, end_month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
        if len(out) > 240:
            break
    return out


def default_period_window(*, now: Optional[datetime] = None) -> Tuple[str, str]:
    current = now or _utcnow()
    end_year = current.year
    end_month = current.month + 2
    while end_month > 12:
        end_month -= 12
        end_year += 1
    start_year = current.year
    start_month = current.month - 17
    while start_month <= 0:
        start_month += 12
        start_year -= 1
    return f"{start_year:04d}-{start_month:02d}", f"{end_year:04d}-{end_month:02d}"


async def list_periods(
    db,
    *,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> List[dict]:
    default_start, default_end = default_period_window()
    start_key = normalize_period_key(start) if start else default_start
    end_key = normalize_period_key(end) if end else default_end
    keys = _iter_month_keys(start_key, end_key)
    stored = await db[COLLECTION].find(
        {"period_key": {"$in": keys}},
        {"_id": 0},
    ).to_list(len(keys) + 10)
    by_key = {str(doc.get("period_key")): doc for doc in stored}
    return [public_period(by_key.get(key), period_key=key) for key in keys]


async def acquire_period_writer_claim(db, date: str) -> Dict[str, str]:
    """Acquire a fresh writer lease for the journal date's month. Missing row = open."""
    period_key = period_key_from_date(date)
    token = str(uuid.uuid4())
    now_dt = _utcnow()
    expires_at = _iso(now_dt + timedelta(seconds=WRITER_CLAIM_TTL_SECONDS))
    claim = {"token": token, "expires_at": expires_at}
    now = _iso(now_dt)

    for _ in range(8):
        updated = await db[COLLECTION].find_one_and_update(
            {"period_key": period_key, "status": STATUS_OPEN},
            {
                "$push": {"writer_claims": claim},
                "$set": {"updated_at": now},
            },
            return_document=True,
        )
        if updated:
            return {"token": token, "period_key": period_key, "expires_at": expires_at}

        existing = await get_period_raw(db, period_key)
        if existing and str(existing.get("status")) == STATUS_CLOSED:
            raise PeriodLockedError(_closed_message(period_key))

        if existing:
            # Open row lost the race (close or concurrent write). Retry.
            continue

        doc = _new_period_doc(period_key, claims=[claim])
        try:
            await db[COLLECTION].insert_one(dict(doc))
            return {"token": token, "period_key": period_key, "expires_at": expires_at}
        except DuplicateKeyError:
            continue

    existing = await get_period_raw(db, period_key)
    if existing and str(existing.get("status")) == STATUS_CLOSED:
        raise PeriodLockedError(_closed_message(period_key))
    raise PeriodLockedError(_closed_message(period_key))


async def release_period_writer_claim(db, period_key: str, token: str) -> None:
    """Token-scoped release only. Never pulls other writers' claims."""
    key = normalize_period_key(period_key)
    tok = str(token or "").strip()
    if not tok:
        return
    await db[COLLECTION].update_one(
        {"period_key": key},
        {
            "$pull": {"writer_claims": {"token": tok}},
            "$set": {"updated_at": now_iso()},
        },
    )


async def require_fresh_writer_claim(db, date: str, token: str) -> Dict[str, str]:
    return await renew_period_writer_claim(db, date, token)


async def renew_period_writer_claim(db, date: str, token: str) -> Dict[str, str]:
    """Atomically prove the period is OPEN and this token is still owned, then refresh TTL.

    Mongo period document is authoritative. Expired-but-still-present tokens may
    refresh only while the period remains open. Closed / removed tokens cannot insert.
    """
    period_key = period_key_from_date(date)
    tok = str(token or "").strip()
    if not tok:
        raise PeriodLockedError(CLAIM_EXPIRED_MESSAGE)
    now_dt = _utcnow()
    now = _iso(now_dt)
    expires_at = _iso(now_dt + timedelta(seconds=WRITER_CLAIM_TTL_SECONDS))
    updated = await db[COLLECTION].find_one_and_update(
        {
            "period_key": period_key,
            "status": STATUS_OPEN,
            "writer_claims.token": tok,
        },
        {
            "$set": {
                "writer_claims.$.expires_at": expires_at,
                "updated_at": now,
            }
        },
        return_document=True,
    )
    if updated:
        return {"token": tok, "period_key": period_key, "expires_at": expires_at}
    existing = await get_period_raw(db, period_key)
    if existing and str(existing.get("status")) == STATUS_CLOSED:
        raise PeriodLockedError(_closed_message(period_key))
    raise PeriodLockedError(CLAIM_EXPIRED_MESSAGE)


async def unresolved_accounting_recoveries(db, period_key: str) -> List[dict]:
    """Durable OB recovery markers whose cutoff/journal date is in this month."""
    start, end = month_bounds(period_key)
    return await db[OB_RECOVERY_COLLECTION].find(
        {
            "accounting_recovery_required": True,
            "cutoff_date": {"$gte": start, "$lte": end},
        },
        {
            "_id": 0,
            "id": 1,
            "number": 1,
            "cutoff_date": 1,
            "accounting_recovery_journal_id": 1,
        },
    ).to_list(50)


def _recovery_close_message(rows: List[dict]) -> str:
    bits = []
    for row in rows[:5]:
        bits.append(
            f"{row.get('number') or row.get('id')} jurnal {row.get('accounting_recovery_journal_id') or '-'}"
        )
    extra = f" {'; '.join(bits)}" if bits else ""
    return PERIOD_RECOVERY_MESSAGE + extra


async def _record_period_event(
    db,
    *,
    event_type: str,
    period_key: str,
    user: Optional[dict],
    before: dict,
    after: dict,
    reason: str,
    operation_key: str,
) -> None:
    """Write one period lifecycle audit event. Idempotent on operation_key."""
    key = str(operation_key or "").strip()
    if not key:
        raise PeriodValidationError("operation_key periode tidak valid")
    existing = await db[AUDIT_COLLECTION].find_one({"operation_key": key}, {"_id": 1})
    if existing:
        return
    try:
        await record_accounting_event(
            db,
            event_type=event_type,
            entity_type=ENTITY_PERIOD,
            entity_id=period_key,
            actor=user,
            before=before,
            after=after,
            reason=reason,
            operation_key=key,
        )
    except DuplicateKeyError:
        return


async def close_period(
    db,
    period_key: str,
    *,
    user: Optional[dict] = None,
    reason: Optional[str] = None,
) -> dict:
    key = normalize_period_key(period_key)
    now = now_iso()
    actor = actor_snapshot(user)
    close_reason = normalize_period_reason(reason)

    existing = await get_period_raw(db, key)
    if existing and str(existing.get("status")) == STATUS_CLOSED:
        stored_reason = str(existing.get("close_reason") or close_reason)
        await _record_period_event(
            db,
            event_type=EVENT_PERIOD_CLOSED,
            period_key=key,
            user=user,
            before={"status": STATUS_OPEN},
            after={"status": STATUS_CLOSED, "closed_at": existing.get("closed_at")},
            reason=stored_reason,
            operation_key=period_close_operation_key(key, existing.get("closed_at")),
        )
        return public_period(existing, period_key=key)

    recoveries = await unresolved_accounting_recoveries(db, key)
    if recoveries:
        raise PeriodConflictError(_recovery_close_message(recoveries))

    start, end = month_bounds(key)
    claimed = await db[COLLECTION].find_one_and_update(
        {
            "period_key": key,
            "status": STATUS_OPEN,
            "writer_claims": {"$not": {"$elemMatch": _fresh_claim_elem(now)}},
        },
        {
            "$set": {
                "status": STATUS_CLOSED,
                "closed_at": now,
                "closed_by": actor,
                "close_reason": close_reason,
                "updated_at": now,
                "start_date": start,
                "end_date": end,
                "writer_claims": [],
            }
        },
        return_document=True,
    )
    if claimed:
        claimed.pop("_id", None)
        await _record_period_event(
            db,
            event_type=EVENT_PERIOD_CLOSED,
            period_key=key,
            user=user,
            before={"status": STATUS_OPEN},
            after={"status": STATUS_CLOSED, "closed_at": claimed.get("closed_at") or now},
            reason=str(claimed.get("close_reason") or close_reason),
            operation_key=period_close_operation_key(key, claimed.get("closed_at") or now),
        )
        return public_period(claimed, period_key=key)

    existing = await get_period_raw(db, key)
    if existing and str(existing.get("status")) == STATUS_CLOSED:
        stored_reason = str(existing.get("close_reason") or close_reason)
        await _record_period_event(
            db,
            event_type=EVENT_PERIOD_CLOSED,
            period_key=key,
            user=user,
            before={"status": STATUS_OPEN},
            after={"status": STATUS_CLOSED, "closed_at": existing.get("closed_at")},
            reason=stored_reason,
            operation_key=period_close_operation_key(key, existing.get("closed_at")),
        )
        return public_period(existing, period_key=key)
    if existing and _has_fresh_claims(existing, now=now):
        raise PeriodConflictError(PERIOD_WRITERS_MESSAGE)
    if existing:
        raise PeriodConflictError(PERIOD_WRITERS_MESSAGE)

    doc = _new_period_doc(key)
    doc.update({
        "status": STATUS_CLOSED,
        "closed_at": now,
        "closed_by": actor,
        "close_reason": close_reason,
        "updated_at": now,
        "writer_claims": [],
    })
    try:
        await db[COLLECTION].insert_one(dict(doc))
    except DuplicateKeyError:
        return await close_period(db, key, user=user, reason=reason)
    doc.pop("_id", None)
    await _record_period_event(
        db,
        event_type=EVENT_PERIOD_CLOSED,
        period_key=key,
        user=user,
        before={"status": STATUS_OPEN},
        after={"status": STATUS_CLOSED, "closed_at": doc.get("closed_at") or now},
        reason=str(doc.get("close_reason") or close_reason),
        operation_key=period_close_operation_key(key, doc.get("closed_at") or now),
    )
    return public_period(doc, period_key=key)


async def reopen_period(
    db,
    period_key: str,
    *,
    user: Optional[dict] = None,
    reason: Optional[str] = None,
) -> dict:
    key = normalize_period_key(period_key)
    now = now_iso()
    actor = actor_snapshot(user)
    reopen_reason = normalize_period_reason(reason)

    existing = await get_period_raw(db, key)
    if not existing or str(existing.get("status")) != STATUS_CLOSED:
        if existing and str(existing.get("status")) == STATUS_OPEN and existing.get("reopened_at"):
            stored_reason = str(existing.get("reopen_reason") or reopen_reason)
            await _record_period_event(
                db,
                event_type=EVENT_PERIOD_REOPENED,
                period_key=key,
                user=user,
                before={"status": STATUS_CLOSED},
                after={"status": STATUS_OPEN, "reopened_at": existing.get("reopened_at")},
                reason=stored_reason,
                operation_key=period_reopen_operation_key(key, existing.get("reopened_at")),
            )
        return public_period(existing, period_key=key)

    updated = await db[COLLECTION].find_one_and_update(
        {"period_key": key, "status": STATUS_CLOSED},
        {
            "$set": {
                "status": STATUS_OPEN,
                "reopened_at": now,
                "reopened_by": actor,
                "reopen_reason": reopen_reason,
                "updated_at": now,
            }
        },
        return_document=True,
    )
    if not updated:
        current = await get_period_raw(db, key)
        if current and str(current.get("status")) == STATUS_OPEN and current.get("reopened_at"):
            stored_reason = str(current.get("reopen_reason") or reopen_reason)
            await _record_period_event(
                db,
                event_type=EVENT_PERIOD_REOPENED,
                period_key=key,
                user=user,
                before={"status": STATUS_CLOSED},
                after={"status": STATUS_OPEN, "reopened_at": current.get("reopened_at")},
                reason=stored_reason,
                operation_key=period_reopen_operation_key(key, current.get("reopened_at")),
            )
        return public_period(current, period_key=key)
    updated.pop("_id", None)
    await _record_period_event(
        db,
        event_type=EVENT_PERIOD_REOPENED,
        period_key=key,
        user=user,
        before={"status": STATUS_CLOSED},
        after={"status": STATUS_OPEN, "reopened_at": updated.get("reopened_at") or now},
        reason=str(updated.get("reopen_reason") or reopen_reason),
        operation_key=period_reopen_operation_key(key, updated.get("reopened_at") or now),
    )
    return public_period(updated, period_key=key)
