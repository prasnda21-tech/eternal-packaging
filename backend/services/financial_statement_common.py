"""Shared read-only helpers for financial statements.

Zero-write. No posting, no journal mutation, no materialized reports.
Date parsing, complete COA loading, and complete journal-line aggregation
only. Batch sizes are internal processing sizes, never financial caps.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional, Tuple

from services.chart_of_accounts import COLLECTION as COA_COLLECTION, public_account
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    STATUS_REVERSED,
)

# Internal processing sizes only. Not financial truncation limits.
JOURNAL_ID_BATCH_SIZE = 500
ACCOUNT_READ_BATCH_SIZE = 500

LEGITIMATE_STATUSES = frozenset({STATUS_POSTED, STATUS_REVERSED})
MONEY_Q = Decimal("0.01")

ACCOUNT_PROJECTION = {
    "_id": 0,
    "id": 1,
    "code": 1,
    "name": 1,
    "account_type": 1,
}

LINE_PROJECTION = {
    "_id": 0,
    "account_id": 1,
    "debit": 1,
    "credit": 1,
}

JOURNAL_PROJECTION = {
    "_id": 0,
    "id": 1,
    "status": 1,
}


class FinancialStatementDateError(ValueError):
    """Invalid financial business date."""


def money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw or 0)).quantize(MONEY_Q)
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0.00")


def money_float(amount: Decimal) -> float:
    return float(amount)


def journal_is_legitimate(doc: Optional[dict]) -> bool:
    status = str((doc or {}).get("status") or "").strip()
    if not status:
        return True
    return status in LEGITIMATE_STATUSES


def parse_financial_date(
    raw: Any,
    *,
    empty_message: str,
    invalid_message: str,
    error_cls: type[Exception] = FinancialStatementDateError,
) -> str:
    """Accept only a real calendar YYYY-MM-DD. No timezone conversion."""
    text = str(raw if raw is not None else "").strip()
    if not text:
        raise error_cls(empty_message)
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise error_cls(invalid_message) from exc
    normalized = parsed.isoformat()
    if text != normalized:
        raise error_cls(invalid_message)
    return normalized


def parse_financial_date_range(
    start_raw: Any,
    end_raw: Any,
    *,
    empty_message: str,
    start_invalid_message: str,
    end_invalid_message: str,
    order_message: str,
    error_cls: type[Exception] = FinancialStatementDateError,
) -> Tuple[str, str]:
    start_text = str(start_raw if start_raw is not None else "").strip()
    end_text = str(end_raw if end_raw is not None else "").strip()
    if not start_text or not end_text:
        raise error_cls(empty_message)
    start = parse_financial_date(
        start_text,
        empty_message=empty_message,
        invalid_message=start_invalid_message,
        error_cls=error_cls,
    )
    end = parse_financial_date(
        end_text,
        empty_message=empty_message,
        invalid_message=end_invalid_message,
        error_cls=error_cls,
    )
    if start > end:
        raise error_cls(order_message)
    return start, end


def _accounts_from_docs(docs: List[dict]) -> List[dict]:
    items: List[dict] = []
    for doc in docs:
        public = public_account(doc)
        if not public:
            continue
        aid = str(public.get("id") or "").strip()
        if not aid:
            continue
        items.append(public)
    return items


async def load_statement_accounts(db) -> List[dict]:
    """Load every ledger account. No lock checks. No arbitrary to_list cap."""
    items: List[dict] = []
    batch: List[dict] = []
    batch_size = ACCOUNT_READ_BATCH_SIZE
    cursor = db[COA_COLLECTION].find({}, ACCOUNT_PROJECTION)
    async for doc in cursor:
        batch.append(doc)
        if len(batch) >= batch_size:
            items.extend(_accounts_from_docs(batch))
            batch = []
    if batch:
        items.extend(_accounts_from_docs(batch))
    items.sort(key=lambda row: (str(row.get("code") or ""), str(row.get("id") or "")))
    return items


async def _accumulate_line_batch(
    db,
    journal_ids: List[str],
    out: Dict[str, Tuple[Decimal, Decimal]],
) -> None:
    if not journal_ids:
        return
    cursor = db[JOURNAL_LINES].find(
        {"journal_id": {"$in": journal_ids}},
        LINE_PROJECTION,
    )
    async for ln in cursor:
        aid = str(ln.get("account_id") or "").strip()
        if not aid:
            continue
        debit, credit = out.get(aid, (Decimal("0.00"), Decimal("0.00")))
        out[aid] = (debit + money(ln.get("debit")), credit + money(ln.get("credit")))


async def sum_journal_lines_by_account(
    db,
    date_query: Dict[str, Any],
) -> Dict[str, Tuple[Decimal, Decimal]]:
    """Aggregate debit/credit by account for legitimate journals in date_query.

    Streams journal headers, then loads lines in deterministic bounded ID
    batches. Every batch is processed; no financial row is dropped.
    """
    out: Dict[str, Tuple[Decimal, Decimal]] = {}
    batch: List[str] = []
    batch_size = JOURNAL_ID_BATCH_SIZE
    cursor = db[JOURNAL_ENTRIES].find(date_query, JOURNAL_PROJECTION)
    async for doc in cursor:
        jid = str(doc.get("id") or "").strip()
        if not jid or not journal_is_legitimate(doc):
            continue
        batch.append(jid)
        if len(batch) >= batch_size:
            await _accumulate_line_batch(db, batch, out)
            batch = []
    if batch:
        await _accumulate_line_batch(db, batch, out)
    return out
