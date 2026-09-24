"""Minimal double-entry journal engine for Kas & Bank Phase 1.

Not wired to Sales / Purchasing / Payroll / HPP / Stock / Invoice modules.
"""
from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Sequence, Tuple

from contextlib import asynccontextmanager

from pymongo.errors import DuplicateKeyError

from services.accounting_periods import (
    PeriodLockedError,
    PeriodValidationError,
    acquire_period_writer_claim,
    release_period_writer_claim,
    renew_period_writer_claim,
)
from services.purchase_common import (
    get_named_lock,
    next_dated_seq,
    now_iso,
)

JOURNAL_ENTRIES = "journal_entries"
JOURNAL_LINES = "journal_lines"
PREFIX_JOURNAL = "JE"

STATUS_POSTED = "posted"
STATUS_REVERSED = "reversed"
STATUS_INCOMPLETE = "incomplete"
REVERSAL_INCOMPLETE = "REVERSAL_INCOMPLETE"

SOURCE_RECEIPT = "cash_bank_receipt"
SOURCE_PAYMENT = "cash_bank_payment"
SOURCE_TRANSFER = "cash_bank_transfer"
SOURCE_REVERSAL = "cash_bank_reversal"


class JournalValidationError(ValueError):
    """Invalid journal payload (balance, amounts, lines)."""


class JournalConflictError(Exception):
    """Duplicate source journal or repeated reversal."""


async def _before_reversal_insert() -> None:
    """Seam after the last consumption check and immediately before the reversal write."""
    return None


async def _after_reversal_status_commit() -> None:
    """Seam after the source status CAS and before the reversal header insert."""
    return None


async def _after_reversal_header_insert() -> None:
    """Seam after the reversal header is durable and before its lines are written."""
    return None


async def _after_reversal_line_inserted(_index: int) -> None:
    """Seam after one reversal line is durable."""
    return None


async def _bind_reversal_owner(db, journal_id: str, tokens: List[str], expires_at: str) -> None:
    """Copy the live item-lock lease onto the source journal.

    Standalone Mongo cannot bind the later insert to another collection.
    The lease deadline stored here is what the reversal write filters on.
    """
    from services.inventory_financial_locks import _utc_now

    now = _utc_now().isoformat()
    result = await db[JOURNAL_ENTRIES].update_one(
        {
            "id": journal_id,
            "status": STATUS_POSTED,
            "$or": [
                {"reversal_owner_tokens": {"$exists": False}},
                {"reversal_owner_tokens": None},
                {"reversal_owner_tokens": []},
                {"reversal_owner_expires_at": {"$exists": False}},
                {"reversal_owner_expires_at": None},
                {"reversal_owner_expires_at": ""},
                {"reversal_owner_expires_at": {"$lte": now}},
                {"reversal_owner_tokens": tokens},
            ],
        },
        {"$set": {
            "reversal_owner_tokens": list(tokens),
            "reversal_owner_expires_at": expires_at,
        }},
    )
    if int(getattr(result, "matched_count", 0) or 0) != 1:
        raise JournalConflictError("Kunci item opening tidak lagi sah.")


async def _commit_reversal_owner(db, journal_id: str, tokens: List[str], intent: dict) -> bool:
    """Flip the source journal only while its copied lease is still unexpired.

    The same update stores the reversal operation. A crashed process leaves
    that marker; it does not rely on an in-process exception handler.
    """
    from services.inventory_financial_locks import _utc_now

    now = _utc_now().isoformat()
    result = await db[JOURNAL_ENTRIES].update_one(
        {
            "id": journal_id,
            "status": STATUS_POSTED,
            "reversal_owner_tokens": list(tokens),
            "reversal_owner_expires_at": {"$gt": now},
            "$or": [
                {"reversal_intent": {"$exists": False}},
                {"reversal_intent": None},
            ],
        },
        {"$set": {
            "status": STATUS_REVERSED,
            "reversal_intent": intent,
        }},
    )
    return int(getattr(result, "matched_count", 0) or 0) == 1


def _lines_match_intent(stored: Sequence[dict], intent_lines: Sequence[dict]) -> bool:
    if len(list(stored or [])) != len(list(intent_lines or [])):
        return False
    by_id = {str(row.get("id") or ""): row for row in stored or []}
    total_dr = Decimal("0.00")
    total_cr = Decimal("0.00")
    for wanted in intent_lines or []:
        got = by_id.get(str(wanted.get("id") or ""))
        if not got:
            return False
        if str(got.get("account_id") or "") != str(wanted.get("account_id") or ""):
            return False
        debit = _money(got.get("debit") or 0)
        credit = _money(got.get("credit") or 0)
        if debit != _money(wanted.get("debit") or 0) or credit != _money(wanted.get("credit") or 0):
            return False
        total_dr += debit
        total_cr += credit
    return total_dr == total_cr and total_dr > 0


def _balanced_lines(lines: Sequence[dict]) -> bool:
    rows = list(lines or [])
    if len(rows) < 2:
        return False
    total_dr = Decimal("0.00")
    total_cr = Decimal("0.00")
    for row in rows:
        total_dr += _money(row.get("debit") or 0)
        total_cr += _money(row.get("credit") or 0)
    return total_dr == total_cr and total_dr > 0


async def reversal_is_complete(db, journal: Optional[dict]) -> bool:
    """Read-only. Incomplete reversal headers are not success."""
    if not journal:
        return False
    intent = journal.get("reversal_intent") or {}
    phase = str(intent.get("phase") or "")
    header = await db[JOURNAL_ENTRIES].find_one({"reversal_of": journal.get("id")}, {"_id": 0})
    if intent:
        if phase != "complete" or not header or str(header.get("status") or "") != STATUS_POSTED:
            return False
        lines = await db[JOURNAL_LINES].find(
            {"journal_id": header.get("id")}, {"_id": 0}
        ).to_list(100)
        return _lines_match_intent(lines, intent.get("lines") or [])
    if str(journal.get("status") or "") != STATUS_REVERSED or not header:
        return False
    if str(header.get("status") or "") != STATUS_POSTED:
        return False
    lines = await db[JOURNAL_LINES].find(
        {"journal_id": header.get("id")}, {"_id": 0}
    ).to_list(100)
    return _balanced_lines(lines)


async def _ensure_reversal_header(db, original: dict, intent: dict) -> dict:
    oid = str(original.get("id") or "")
    existing = await db[JOURNAL_ENTRIES].find_one({"reversal_of": oid}, {"_id": 0})
    if not existing:
        existing = await db[JOURNAL_ENTRIES].find_one(
            {"id": intent.get("reversal_journal_id")}, {"_id": 0}
        )
    if existing:
        return existing
    now = now_iso()
    header = {
        "id": str(intent.get("reversal_journal_id") or ""),
        "number": "",
        "date": str(intent.get("date") or ""),
        "source_type": str(intent.get("source_type") or SOURCE_REVERSAL),
        "source_id": str(intent.get("source_id") or ""),
        "description": str(intent.get("description") or ""),
        "status": STATUS_INCOMPLETE,
        "reversal_of": oid,
        "reversal_operation_id": str(intent.get("operation_id") or ""),
        "posted_at": now,
        "posted_by": str(intent.get("posted_by") or ""),
        "created_at": now,
    }
    if not header["id"]:
        raise JournalConflictError("Reversal belum lengkap")
    last_err = None
    for _ in range(25):
        header["number"] = await next_dated_seq(db, JOURNAL_ENTRIES, "number", PREFIX_JOURNAL)
        try:
            await db[JOURNAL_ENTRIES].insert_one(dict(header))
            header.pop("_id", None)
            return header
        except DuplicateKeyError as exc:
            last_err = exc
            msg = str(exc)
            if "uniq_reversal_of" in msg or "reversal_of" in msg:
                found = await db[JOURNAL_ENTRIES].find_one({"reversal_of": oid}, {"_id": 0})
                if found:
                    return found
            if "id" in msg:
                found = await db[JOURNAL_ENTRIES].find_one({"id": header["id"]}, {"_id": 0})
                if found:
                    return found
            if "number" in msg or "E11000" in msg:
                continue
            raise JournalConflictError("Reversal sudah ada") from exc
    raise JournalConflictError(f"Gagal mengalokasikan nomor jurnal. {last_err}")


async def _ensure_reversal_line(db, journal_id: str, line: dict) -> None:
    line_id = str(line.get("id") or "")
    if not line_id:
        raise JournalConflictError("Reversal belum lengkap")
    existing = await db[JOURNAL_LINES].find_one({"id": line_id}, {"_id": 1})
    if existing:
        return
    try:
        await db[JOURNAL_LINES].insert_one({
            "id": line_id,
            "journal_id": journal_id,
            "account_id": str(line.get("account_id") or ""),
            "debit": line.get("debit") or 0,
            "credit": line.get("credit") or 0,
            "description": line.get("description"),
            "created_at": now_iso(),
        })
    except DuplicateKeyError:
        found = await db[JOURNAL_LINES].find_one({"id": line_id}, {"_id": 1})
        if not found:
            raise


async def _materialize_reversal_intent(db, journal_id: str, writer_claim_token: Optional[str] = None) -> dict:
    """Finish the durable reversal. Never deletes a header or a line."""
    original = await db[JOURNAL_ENTRIES].find_one({"id": str(journal_id)}, {"_id": 0})
    intent = (original or {}).get("reversal_intent") or {}
    if not intent.get("operation_id") or not intent.get("lines"):
        raise JournalConflictError("Reversal belum lengkap")
    async with _period_writer_guard(db, str(intent.get("date") or ""), writer_claim_token):
        header = await _ensure_reversal_header(db, original, intent)
        await db[JOURNAL_ENTRIES].update_one(
            {
                "id": str(journal_id),
                "reversal_intent.operation_id": intent.get("operation_id"),
            },
            {"$set": {"reversal_intent.phase": "header_written"}},
        )
        await _after_reversal_header_insert()
        for index, line in enumerate(intent.get("lines") or []):
            await _ensure_reversal_line(db, str(header.get("id") or ""), line)
            await _after_reversal_line_inserted(index)
        stored = await db[JOURNAL_LINES].find(
            {"journal_id": header.get("id")}, {"_id": 0}
        ).to_list(100)
        if not _lines_match_intent(stored, intent.get("lines") or []):
            raise JournalConflictError("Reversal belum lengkap")
        posted = await db[JOURNAL_ENTRIES].update_one(
            {
                "id": str(header.get("id") or ""),
                "reversal_of": str(journal_id),
                "status": {"$in": [STATUS_INCOMPLETE, STATUS_POSTED]},
            },
            {"$set": {"status": STATUS_POSTED}},
        )
        if int(getattr(posted, "matched_count", 0) or 0) != 1:
            raise JournalConflictError("Reversal belum lengkap")
        header["status"] = STATUS_POSTED
        phase = await db[JOURNAL_ENTRIES].update_one(
            {
                "id": str(journal_id),
                "status": STATUS_REVERSED,
                "reversal_intent.operation_id": intent.get("operation_id"),
            },
            {"$set": {"reversal_intent.phase": "complete"}},
        )
        if int(getattr(phase, "matched_count", 0) or 0) != 1:
            raise JournalConflictError("Reversal belum lengkap")
        fresh = await db[JOURNAL_ENTRIES].find_one({"id": str(journal_id)}, {"_id": 0})
        if not await reversal_is_complete(db, fresh):
            raise JournalConflictError("Reversal belum lengkap")
        return public_journal(header, stored)


async def _restore_posted_without_reversal_header(db, journal_id: str) -> bool:
    """Return the source to posted only when no reversal header exists."""
    header = await db[JOURNAL_ENTRIES].find_one({"reversal_of": str(journal_id)}, {"_id": 1})
    if header:
        return False
    result = await db[JOURNAL_ENTRIES].update_one(
        {
            "id": str(journal_id),
            "status": STATUS_REVERSED,
            "reversal_intent": {"$in": [None]},
        },
        {"$set": {"status": STATUS_POSTED}, "$unset": {"reversal_intent": ""}},
    )
    if int(getattr(result, "matched_count", 0) or 0) == 1:
        return True
    missing_intent = await db[JOURNAL_ENTRIES].update_one(
        {
            "id": str(journal_id),
            "status": STATUS_REVERSED,
            "reversal_intent": {"$exists": False},
        },
        {"$set": {"status": STATUS_POSTED}},
    )
    return int(getattr(missing_intent, "matched_count", 0) or 0) == 1


async def finish_interrupted_reversal(
    db,
    journal_id: str,
    *,
    posted_by: Optional[dict] = None,
    held_item_locks: Optional[list] = None,
) -> Optional[dict]:
    """Explicit recovery after a crashed reversal. GET paths must not call this."""
    oid = str(journal_id or "").strip()
    original = await db[JOURNAL_ENTRIES].find_one({"id": oid}, {"_id": 0})
    if not original:
        raise KeyError("missing")
    intent = original.get("reversal_intent") or {}
    if await reversal_is_complete(db, original):
        header = await db[JOURNAL_ENTRIES].find_one({"reversal_of": oid}, {"_id": 0})
        lines = await db[JOURNAL_LINES].find(
            {"journal_id": (header or {}).get("id")}, {"_id": 0}
        ).to_list(100)
        return public_journal(header, lines)
    if not intent:
        if str(original.get("status") or "") != STATUS_REVERSED:
            raise JournalConflictError("Reversal belum lengkap")
        header = await db[JOURNAL_ENTRIES].find_one({"reversal_of": oid}, {"_id": 0})
        if not header:
            await _restore_posted_without_reversal_header(db, oid)
            raise JournalConflictError("Jurnal sudah dibatalkan")
        lines = await db[JOURNAL_LINES].find(
            {"journal_id": header.get("id")}, {"_id": 0}
        ).to_list(100)
        if _balanced_lines(lines) and str(header.get("status") or "") == STATUS_POSTED:
            return public_journal(header, lines)
        raise JournalConflictError("Reversal belum lengkap")

    from services.financial_inventory_opening import opening_consumption_lock_keys
    from services.inventory_financial_locks import (
        acquire_item_locks,
        release_item_locks,
        renew_item_locks,
    )

    keys = await opening_consumption_lock_keys(db, original)
    owned = None
    if keys:
        if held_item_locks:
            renewed = await renew_item_locks(db, held_item_locks)
            if not renewed:
                raise JournalConflictError("Kunci item opening tidak lagi sah.")
        else:
            from services.inventory_financial_locks import ensure_indexes as ensure_lock_indexes

            await ensure_lock_indexes(db)
            owned = await acquire_item_locks(db, keys, f"journal-recover:{oid}")
            if owned is None:
                raise JournalConflictError(
                    "Item persediaan sedang dikunci oleh proses accounting lain."
                )
    try:
        return await _materialize_reversal_intent(db, oid)
    finally:
        if owned:
            await release_item_locks(db, owned)


def _money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw)).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise JournalValidationError("Nominal tidak valid") from exc


def _money_float(d: Decimal) -> float:
    return float(d)


def validate_lines(lines: Sequence[dict]) -> Tuple[Decimal, Decimal, List[dict]]:
    if not lines or len(lines) < 2:
        raise JournalValidationError("Jurnal minimal 2 baris")
    total_dr = Decimal("0.00")
    total_cr = Decimal("0.00")
    normalized: List[dict] = []
    for raw in lines:
        account_id = str((raw or {}).get("account_id") or "").strip()
        if not account_id:
            raise JournalValidationError("account_id wajib diisi pada setiap baris jurnal")
        debit = _money((raw or {}).get("debit") or 0)
        credit = _money((raw or {}).get("credit") or 0)
        if debit < 0 or credit < 0:
            raise JournalValidationError("Debit/kredit tidak boleh negatif")
        if debit > 0 and credit > 0:
            raise JournalValidationError("Satu baris tidak boleh debit dan kredit sekaligus")
        if debit == 0 and credit == 0:
            raise JournalValidationError("Nominal baris harus lebih besar dari 0")
        if debit <= 0 and credit <= 0:
            raise JournalValidationError("Nominal baris harus lebih besar dari 0")
        total_dr += debit
        total_cr += credit
        normalized.append({
            "id": str(uuid.uuid4()),
            "account_id": account_id,
            "debit": _money_float(debit),
            "credit": _money_float(credit),
            "description": str((raw or {}).get("description") or "").strip() or None,
        })
    if total_dr != total_cr:
        raise JournalValidationError(
            f"Jurnal tidak seimbang: debit {total_dr} ≠ kredit {total_cr}"
        )
    if total_dr <= 0:
        raise JournalValidationError("Total jurnal harus lebih besar dari 0")
    return total_dr, total_cr, normalized


async def ensure_indexes(db) -> None:
    entries = db[JOURNAL_ENTRIES]
    lines = db[JOURNAL_LINES]
    await entries.create_index("id", unique=True)
    await entries.create_index("number", unique=True)
    await entries.create_index("source_type")
    await entries.create_index("source_id")
    await entries.create_index("date")
    # One primary (non-reversal) posted journal per source transaction.
    await entries.create_index(
        [("source_type", 1), ("source_id", 1)],
        unique=True,
        partialFilterExpression={
            "status": STATUS_POSTED,
            "reversal_of": None,
        },
        name="uniq_primary_source_journal",
    )
    # At most one reversal journal pointing at an original.
    await entries.create_index(
        "reversal_of",
        unique=True,
        partialFilterExpression={"reversal_of": {"$type": "string"}},
        name="uniq_reversal_of",
    )
    await lines.create_index("id", unique=True)
    await lines.create_index("journal_id")
    await lines.create_index("account_id")


def _present_journal(doc: Optional[dict], lines: Optional[List[dict]] = None) -> Optional[dict]:
    """Read-only view. An unfinished reversal is not a posted journal."""
    out = public_journal(doc, lines)
    if not out or not doc:
        return out
    status = str(doc.get("status") or "")
    intent = doc.get("reversal_intent") or {}
    phase = str(intent.get("phase") or "")
    open_intent = bool(intent.get("operation_id")) and phase != "complete"
    partial_header = (
        bool(doc.get("reversal_of"))
        and lines is not None
        and not _balanced_lines(lines)
    )
    if status == STATUS_INCOMPLETE or open_intent or partial_header:
        out["status"] = STATUS_INCOMPLETE
        out["blocker_code"] = REVERSAL_INCOMPLETE
    return out


def public_journal(doc: Optional[dict], lines: Optional[List[dict]] = None) -> Optional[dict]:
    if not doc:
        return None
    out = {
        "id": doc.get("id"),
        "number": doc.get("number"),
        "date": doc.get("date"),
        "source_type": doc.get("source_type"),
        "source_id": doc.get("source_id"),
        "description": doc.get("description") or "",
        "status": doc.get("status"),
        "reversal_of": doc.get("reversal_of"),
        "posted_at": doc.get("posted_at"),
        "posted_by": doc.get("posted_by"),
        "created_at": doc.get("created_at"),
    }
    if lines is not None:
        out["lines"] = [
            {
                "id": ln.get("id"),
                "journal_id": ln.get("journal_id"),
                "account_id": ln.get("account_id"),
                "debit": ln.get("debit"),
                "credit": ln.get("credit"),
                "description": ln.get("description"),
            }
            for ln in lines
        ]
    return out


async def get_journal(db, journal_id: str, *, with_lines: bool = True) -> Optional[dict]:
    doc = await db[JOURNAL_ENTRIES].find_one({"id": str(journal_id)}, {"_id": 0})
    if not doc:
        return None
    lines = None
    if with_lines:
        lines = await db[JOURNAL_LINES].find(
            {"journal_id": doc["id"]}, {"_id": 0}
        ).sort("debit", -1).to_list(100)
    return _present_journal(doc, lines)


async def find_primary_journal_for_source(
    db, source_type: str, source_id: str
) -> Optional[dict]:
    doc = await db[JOURNAL_ENTRIES].find_one(
        {
            "source_type": source_type,
            "source_id": str(source_id),
            "reversal_of": None,
        },
        {"_id": 0},
    )
    if not doc:
        return None
    lines = await db[JOURNAL_LINES].find(
        {"journal_id": doc["id"]}, {"_id": 0}
    ).to_list(100)
    return public_journal(doc, lines)


async def _insert_journal_atomic(
    db,
    *,
    date: str,
    source_type: str,
    source_id: str,
    description: str,
    lines: Sequence[dict],
    posted_by: Optional[dict],
    reversal_of: Optional[str] = None,
    status: str = STATUS_POSTED,
    writer_claim_token: Optional[str] = None,
) -> dict:
    """Insert header + lines. Compensating delete on line failure. Caller holds lock."""
    _, _, normalized = validate_lines(lines)
    now = now_iso()
    actor = ""
    if posted_by:
        actor = str(
            posted_by.get("email")
            or posted_by.get("name")
            or posted_by.get("id")
            or ""
        ).strip()

    journal_id = str(uuid.uuid4())
    header = {
        "id": journal_id,
        "number": "",
        "date": str(date or "").strip(),
        "source_type": source_type,
        "source_id": str(source_id),
        "description": str(description or "").strip(),
        "status": status,
        "reversal_of": reversal_of,
        "posted_at": now,
        "posted_by": actor,
        "created_at": now,
    }

    saved = None
    last_err = None
    for _ in range(25):
        header["number"] = await next_dated_seq(db, JOURNAL_ENTRIES, "number", PREFIX_JOURNAL)
        try:
            await renew_period_writer_claim(db, date, writer_claim_token)
            await db[JOURNAL_ENTRIES].insert_one(dict(header))
            header.pop("_id", None)
            saved = header
            break
        except DuplicateKeyError as exc:
            last_err = exc
            msg = str(exc)
            if "uniq_primary_source_journal" in msg or "source_type" in msg or "source_id" in msg:
                raise JournalConflictError("Jurnal untuk sumber ini sudah ada") from exc
            if "uniq_reversal_of" in msg or "reversal_of" in msg:
                raise JournalConflictError("Reversal sudah ada") from exc
            if "number" in msg or "E11000" in msg:
                continue
            raise JournalConflictError("Jurnal duplikat") from exc
    if not saved:
        raise JournalConflictError(f"Gagal mengalokasikan nomor jurnal. {last_err}")

    line_docs = []
    for ln in normalized:
        line_docs.append({
            **ln,
            "journal_id": saved["id"],
            "created_at": now,
        })

    try:
        if line_docs:
            await db[JOURNAL_LINES].insert_many(line_docs)
    except Exception:
        # Compensating cleanup — exact IDs only
        await db[JOURNAL_LINES].delete_many({"journal_id": saved["id"]})
        await db[JOURNAL_ENTRIES].delete_one({"id": saved["id"]})
        raise

    return public_journal(saved, line_docs)


@asynccontextmanager
async def _period_writer_guard(db, date: str, writer_claim_token: Optional[str] = None):
    """Gate new journal inserts. Held tokens are not acquired or released here."""
    token = str(writer_claim_token or "").strip()
    if token:
        await renew_period_writer_claim(db, date, token)
        yield token
        return
    claim = await acquire_period_writer_claim(db, date)
    try:
        yield claim["token"]
    finally:
        await release_period_writer_claim(db, claim["period_key"], claim["token"])


async def post_balanced_journal(
    db,
    *,
    date: str,
    source_type: str,
    source_id: str,
    description: str,
    lines: Sequence[dict],
    posted_by: Optional[dict] = None,
    writer_claim_token: Optional[str] = None,
) -> dict:
    """Create one primary posted journal for a source. Idempotent on retry."""
    existing = await find_primary_journal_for_source(db, source_type, source_id)
    if existing:
        if existing.get("status") in (STATUS_POSTED, STATUS_REVERSED):
            return existing
        raise JournalConflictError("Jurnal sumber bentrok")

    async with get_named_lock(f"journal_post:{source_type}:{source_id}"):
        existing = await find_primary_journal_for_source(db, source_type, source_id)
        if existing:
            return existing
        try:
            async with _period_writer_guard(db, date, writer_claim_token) as token:
                return await _insert_journal_atomic(
                    db,
                    date=date,
                    source_type=source_type,
                    source_id=source_id,
                    description=description,
                    lines=lines,
                    posted_by=posted_by,
                    reversal_of=None,
                    status=STATUS_POSTED,
                    writer_claim_token=token,
                )
        except PeriodValidationError as exc:
            raise JournalValidationError(str(exc)) from exc
        except PeriodLockedError:
            raise
        except DuplicateKeyError as exc:
            existing = await find_primary_journal_for_source(db, source_type, source_id)
            if existing:
                return existing
            raise JournalConflictError("Jurnal duplikat") from exc


async def reverse_journal(
    db,
    original_journal_id: str,
    *,
    source_type: str,
    source_id: str,
    posted_by: Optional[dict] = None,
    description: Optional[str] = None,
    writer_claim_token: Optional[str] = None,
    held_item_locks: Optional[list] = None,
) -> dict:
    """Create exact opposite journal; mark original reversed. Only once.

    An opening journal keeps the same item locks COGS uses from the
    consumption check through the status update. Callers that already hold
    those locks pass them in so this function renews instead of taking a
    second holder. The reversal itself is an update filtered by that lease
    token and its expiry; after the lease is gone the update matches nothing.
    """
    oid = str(original_journal_id or "").strip()
    if not oid:
        raise JournalValidationError("Jurnal asli tidak valid")

    async with get_named_lock(f"journal_reverse:{oid}"):
        original = await db[JOURNAL_ENTRIES].find_one({"id": oid}, {"_id": 0})
        if not original:
            raise KeyError("missing")
        if original.get("status") == STATUS_REVERSED or (original.get("reversal_intent") or {}).get("phase"):
            return await finish_interrupted_reversal(db, oid, posted_by=posted_by)
        if original.get("status") != STATUS_POSTED:
            raise JournalValidationError("Hanya jurnal posted yang dapat dibatalkan")
        if original.get("reversal_of"):
            raise JournalValidationError("Tidak dapat membatalkan jurnal reversal")
        from services.financial_inventory_opening import (
            guard_consumed_opening_journal,
            opening_consumption_lock_keys,
        )
        from services.inventory_financial_locks import (
            acquire_item_locks,
            release_item_locks,
            renew_item_locks,
        )

        keys = await opening_consumption_lock_keys(db, original)
        owned_locks = None
        if keys:
            if held_item_locks:
                renewed = await renew_item_locks(db, held_item_locks)
                if not renewed:
                    raise JournalConflictError("Kunci item opening tidak lagi sah.")
                held_keys = {
                    str(row.get("financial_inventory_item_key") or "")
                    for row in renewed
                }
                if not set(keys).issubset(held_keys):
                    raise JournalConflictError("Kunci item opening tidak lagi sah.")
            else:
                from services.inventory_financial_locks import ensure_indexes as ensure_lock_indexes

                await ensure_lock_indexes(db)
                owned_locks = await acquire_item_locks(db, keys, f"journal-reverse:{oid}")
                if owned_locks is None:
                    raise JournalConflictError(
                        "Item persediaan sedang dikunci oleh proses accounting lain."
                    )
        try:
            await guard_consumed_opening_journal(db, original)
            renewed_rows = None
            if keys and held_item_locks:
                renewed_rows = await renew_item_locks(db, held_item_locks)
                if not renewed_rows:
                    raise JournalConflictError("Kunci item opening tidak lagi sah.")
            elif owned_locks:
                renewed_rows = await renew_item_locks(db, owned_locks)
                if not renewed_rows:
                    raise JournalConflictError("Kunci item opening tidak lagi sah.")
            await guard_consumed_opening_journal(db, original)
            owner_tokens: Optional[List[str]] = None
            owner_expires = ""
            if renewed_rows:
                owner_tokens = sorted(str(row.get("token") or "") for row in renewed_rows)
                owner_expires = min(str(row.get("expires_at") or "") for row in renewed_rows)
                if not owner_expires or any(not token for token in owner_tokens):
                    raise JournalConflictError("Kunci item opening tidak lagi sah.")

            existing_rev = await db[JOURNAL_ENTRIES].find_one({"reversal_of": oid}, {"_id": 0})
            if existing_rev:
                raise JournalConflictError("Reversal sudah ada")

            orig_lines = await db[JOURNAL_LINES].find(
                {"journal_id": oid}, {"_id": 0}
            ).to_list(100)
            if not orig_lines:
                raise JournalValidationError("Jurnal asli tidak memiliki baris")

            reverse_lines = [
                {
                    "account_id": ln["account_id"],
                    "debit": ln.get("credit") or 0,
                    "credit": ln.get("debit") or 0,
                    "description": ln.get("description"),
                }
                for ln in orig_lines
            ]
            desc = description or f"Reversal {original.get('number') or oid}"
            rev_date = original.get("date") or ""
            if owner_tokens:
                actor = ""
                if posted_by:
                    actor = str(
                        posted_by.get("email")
                        or posted_by.get("name")
                        or posted_by.get("id")
                        or ""
                    ).strip()
                intent = {
                    "operation_id": str(uuid.uuid4()),
                    "phase": "status_committed",
                    "reversal_journal_id": str(uuid.uuid4()),
                    "source_type": source_type or SOURCE_REVERSAL,
                    "source_id": str(source_id),
                    "description": desc,
                    "date": rev_date,
                    "posted_by": actor,
                    "lines": [
                        {
                            "id": str(uuid.uuid4()),
                            "account_id": ln["account_id"],
                            "debit": ln.get("debit") or 0,
                            "credit": ln.get("credit") or 0,
                            "description": ln.get("description"),
                        }
                        for ln in reverse_lines
                    ],
                }
                try:
                    async with _period_writer_guard(db, rev_date, writer_claim_token) as token:
                        await _bind_reversal_owner(db, oid, owner_tokens, owner_expires)
                        await _before_reversal_insert()
                        if not await _commit_reversal_owner(db, oid, owner_tokens, intent):
                            raise JournalConflictError("Kunci item opening tidak lagi sah.")
                        await _after_reversal_status_commit()
                        return await _materialize_reversal_intent(db, oid, token)
                except PeriodValidationError as exc:
                    raise JournalValidationError(str(exc)) from exc
                except PeriodLockedError:
                    raise
            try:
                async with _period_writer_guard(db, rev_date, writer_claim_token) as token:
                    rev_doc = await _insert_journal_atomic(
                        db,
                        date=rev_date,
                        source_type=source_type or SOURCE_REVERSAL,
                        source_id=source_id,
                        description=desc,
                        lines=reverse_lines,
                        posted_by=posted_by,
                        reversal_of=oid,
                        status=STATUS_POSTED,
                        writer_claim_token=token,
                    )
                    await db[JOURNAL_ENTRIES].update_one(
                        {"id": oid, "status": STATUS_POSTED},
                        {"$set": {"status": STATUS_REVERSED}},
                    )
                    return rev_doc
            except PeriodValidationError as exc:
                raise JournalValidationError(str(exc)) from exc
            except PeriodLockedError:
                raise
            except DuplicateKeyError as exc:
                raise JournalConflictError("Reversal sudah ada") from exc
        finally:
            if owned_locks:
                await release_item_locks(db, owned_locks)
