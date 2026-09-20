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

SOURCE_RECEIPT = "cash_bank_receipt"
SOURCE_PAYMENT = "cash_bank_payment"
SOURCE_TRANSFER = "cash_bank_transfer"
SOURCE_REVERSAL = "cash_bank_reversal"


class JournalValidationError(ValueError):
    """Invalid journal payload (balance, amounts, lines)."""


class JournalConflictError(Exception):
    """Duplicate source journal or repeated reversal."""


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
    return public_journal(doc, lines)


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
) -> dict:
    """Create exact opposite journal; mark original reversed. Only once."""
    oid = str(original_journal_id or "").strip()
    if not oid:
        raise JournalValidationError("Jurnal asli tidak valid")

    async with get_named_lock(f"journal_reverse:{oid}"):
        original = await db[JOURNAL_ENTRIES].find_one({"id": oid}, {"_id": 0})
        if not original:
            raise KeyError("missing")
        if original.get("status") == STATUS_REVERSED:
            # Already reversed — return existing reversal if present
            rev = await db[JOURNAL_ENTRIES].find_one({"reversal_of": oid}, {"_id": 0})
            if rev:
                lines = await db[JOURNAL_LINES].find(
                    {"journal_id": rev["id"]}, {"_id": 0}
                ).to_list(100)
                return public_journal(rev, lines)
            raise JournalConflictError("Jurnal sudah dibatalkan")
        if original.get("status") != STATUS_POSTED:
            raise JournalValidationError("Hanya jurnal posted yang dapat dibatalkan")
        if original.get("reversal_of"):
            raise JournalValidationError("Tidak dapat membatalkan jurnal reversal")

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
        except PeriodValidationError as exc:
            raise JournalValidationError(str(exc)) from exc
        except PeriodLockedError:
            raise
        except DuplicateKeyError as exc:
            raise JournalConflictError("Reversal sudah ada") from exc

        await db[JOURNAL_ENTRIES].update_one(
            {"id": oid, "status": STATUS_POSTED},
            {"$set": {"status": STATUS_REVERSED}},
        )
        return rev_doc
