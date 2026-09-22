"""Inventory / COGS reconciliation read model (Phase 5B.1).

Company-wide current recorded state only. Compares:
  B = immutable posted GR/COGS posting bases
  S = inventory-family journal lines
  G = all legitimate journal lines on the same accounts

Zero-write. Explains differences. Does not post, reverse, heal, or rebuild.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.chart_of_accounts import public_account
from services.financial_statement_common import (
    ACCOUNT_READ_BATCH_SIZE,
    JOURNAL_ID_BATCH_SIZE,
    journal_is_legitimate,
    load_statement_accounts,
    money_float,
)
from services.inventory_accounting import (
    CANONICAL_GR,
    CANONICAL_GR_REVERSAL,
    REASON_LEGACY_BEFORE_EFFECTIVE_DATE,
    REASON_NON_INVENTORY_ITEM,
    financial_inventory_item_key,
    readiness_for_receipts,
)
from services.inventory_financial_locks import (
    COLLECTION as LOCKS_COLLECTION,
    lock_is_active,
)
from services.inventory_issue_accounting import (
    ACCOUNTING_CLAIM_TTL_SECONDS,
    CLAIM_AT_FIELD as ISSUE_CLAIM_AT_FIELD,
    CLAIM_FIELD as ISSUE_CLAIM_FIELD,
    CODE_PRE_EFFECTIVE,
    CODE_SERVICE,
    COLLECTION as ISSUE_COLLECTION,
    METHOD_AVG,
    METHOD_FIFO,
    POSTING_BASIS_FIELD,
    SOURCE_TYPE as SOURCE_ISSUE,
    STATUS_BLOCKED,
    STATUS_EXCLUDED,
    STATUS_PENDING,
    STATUS_WAITING,
    acquisition_key,
    evaluate_inventory_issues,
    parse_financial_hpp_method,
)
from services.journal_core import (
    JOURNAL_ENTRIES,
    JOURNAL_LINES,
    STATUS_POSTED,
    STATUS_REVERSED,
)
from services.product_stock_state import (
    COLLECTION as STOCK_COLLECTION,
    STATUS_INITIALIZED,
)
from services.purchase_accounting_settings import get_purchase_accounting_settings
from services.purchase_common import is_cancelled, now_iso
from services.purchase_goods_receipt_posting import (
    ACCOUNTING_POSTED,
    COLLECTION as GR_COLLECTION,
    POSTING_BASIS_FIELD as GR_BASIS_FIELD,
    cancel_claim_is_active,
    posting_claim_is_active,
    recovery_required,
    reverse_claim_is_active,
)

PERM_VIEW = "inventory_reconciliation.view"

SCOPE_CURRENT = "current_recorded_state"
ACCOUNT_TYPE_INVENTORY = "INTR"
ACCOUNT_TYPE_COGS = "COGS"

STATUS_RECONCILED = "RECONCILED"
STATUS_EXPLAINED_DIFFERENCE = "EXPLAINED_DIFFERENCE"
STATUS_DIFFERENCE = "DIFFERENCE"
STATUS_INCOMPLETE = "INCOMPLETE"

STABILITY_STABLE = "STABLE"
STABILITY_IN_PROGRESS = "IN_PROGRESS"
STABILITY_RETRY_REQUIRED = "RETRY_REQUIRED"

ACTIVITY_NONE = "NO_ACTIVITY"
ACTIVITY_PRESENT = "HAS_ACTIVITY"

QTY_MATCHED = "MATCHED"
QTY_DIFFERENCE = "DIFFERENCE"
QTY_MISSING_PHYSICAL = "MISSING_PHYSICAL"
QTY_UOM_MISMATCH = "UOM_MISMATCH"
QTY_UNIT_CONFLICT = "UNIT_CONFLICT"
QTY_INCOMPLETE = "INCOMPLETE"
QTY_IN_PROGRESS = STABILITY_IN_PROGRESS

SOURCE_GR = CANONICAL_GR
SOURCE_GR_REVERSAL = CANONICAL_GR_REVERSAL
INVENTORY_FAMILY = frozenset({SOURCE_GR, SOURCE_GR_REVERSAL, SOURCE_ISSUE})

MONEY_Q = Decimal("0.01")
DOCUMENT_ID_BATCH_SIZE = 400

CODE_POSTED_JOURNAL_MISSING_DOCUMENT = "POSTED_JOURNAL_MISSING_DOCUMENT"
CODE_POSTED_JOURNAL_MISSING_BASIS = "POSTED_JOURNAL_MISSING_BASIS"
CODE_INVALID_ACQUISITION_BASIS = "INVALID_ACQUISITION_BASIS"
CODE_BASIS_JOURNAL_MISMATCH = "BASIS_JOURNAL_MISMATCH"
CODE_CONSUMED_ACQUISITION_MISSING = "CONSUMED_ACQUISITION_MISSING"
CODE_CONSUMED_ACQUISITION_NOT_LIVE = "CONSUMED_ACQUISITION_NOT_LIVE"
CODE_MISSING_COA = "MISSING_COA"
CODE_INVALID_ACCOUNT_CLASSIFICATION = "INVALID_ACCOUNT_CLASSIFICATION"
CODE_NEGATIVE_REMAINING_FINANCIAL = "NEGATIVE_REMAINING_FINANCIAL"
CODE_INCOMPLETE_REVERSAL_PAIR = "INCOMPLETE_REVERSAL_PAIR"
CODE_INCONSISTENT_REVERSAL_PAIR = "INCONSISTENT_REVERSAL_PAIR"
CODE_INVALID_MONETARY_VALUE = "INVALID_MONETARY_VALUE"
CODE_MISSING_JOURNAL_ACCOUNT = "MISSING_JOURNAL_ACCOUNT"
CODE_DOCUMENT_POSTED_WITHOUT_JOURNAL = "DOCUMENT_POSTED_WITHOUT_JOURNAL"
CODE_MISSING_MASTER = "MISSING_MASTER_ITEM"
CODE_UNRESOLVED_ACCOUNT = "UNRESOLVED_ACCOUNT"
CODE_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
CODE_UOM_MISMATCH = "UOM_MISMATCH"
CODE_UNIT_CONFLICT = "UNIT_CONFLICT"
CODE_PHYSICAL_STOCK_IN_PROGRESS = "PHYSICAL_STOCK_IN_PROGRESS"

FLAG_PENDING_GR = "PENDING_GR"
FLAG_PENDING_COGS = "PENDING_COGS"
FLAG_WAITING_EARLIER_ISSUE = "WAITING_EARLIER_ISSUE"
FLAG_BLOCKED_COGS = "BLOCKED_COGS"
FLAG_PRE_EFFECTIVE = "PRE_EFFECTIVE_LEGACY"
FLAG_EXCLUDED_SERVICE = "EXCLUDED_SERVICE"
FLAG_RECOVERY_REQUIRED = "RECOVERY_REQUIRED"

JOURNAL_HEADER_PROJECTION = {
    "_id": 0,
    "id": 1,
    "status": 1,
    "source_type": 1,
    "source_id": 1,
    "reversal_of": 1,
    "date": 1,
    "number": 1,
}
JOURNAL_LINE_PROJECTION = {
    "_id": 0,
    "journal_id": 1,
    "account_id": 1,
    "debit": 1,
    "credit": 1,
}


class InvalidMoneyError(ValueError):
    """Malformed or non-finite monetary value."""


class InvalidQtyError(ValueError):
    """Malformed or non-finite quantity."""


def _text(raw: Any) -> str:
    return str(raw if raw is not None else "").strip()


def _as_dict_list(raw: Any) -> Optional[List[dict]]:
    """Return a list of dicts, empty list when the field is absent, else None if malformed."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        return None
    for item in raw:
        if not isinstance(item, dict):
            return None
    return raw


def parse_money(raw: Any, *, field: str = "amount") -> Decimal:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        raise InvalidMoneyError(f"{field} kosong")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidMoneyError(f"{field} tidak valid") from exc
    if not value.is_finite():
        raise InvalidMoneyError(f"{field} tidak finite")
    return value.quantize(MONEY_Q)


def parse_qty(raw: Any, *, field: str = "qty") -> Decimal:
    if raw is None or (isinstance(raw, str) and not str(raw).strip()):
        raise InvalidQtyError(f"{field} kosong")
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidQtyError(f"{field} tidak valid") from exc
    if not value.is_finite():
        raise InvalidQtyError(f"{field} tidak finite")
    return value


def _qty_float(value: Optional[Decimal]) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _normalize_uom(raw: Any) -> str:
    return _text(raw).upper()


def _pending_event_is_active(state: Optional[dict]) -> bool:
    """True only for a stored in-flight pending_event. Empty/null is stable."""
    pending = (state or {}).get("pending_event")
    if pending is None:
        return False
    if isinstance(pending, dict):
        return bool(pending)
    if isinstance(pending, (list, tuple, set)):
        return bool(pending)
    if isinstance(pending, str):
        return bool(pending.strip())
    return bool(pending)


def _money_zero() -> Decimal:
    return Decimal("0.00")


def _add_money(bucket: Dict[str, Decimal], key: str, amount: Decimal) -> None:
    if not key:
        return
    bucket[key] = money_q(bucket.get(key, _money_zero()) + amount)


def _add_qty(bucket: Dict[str, Decimal], key: str, amount: Decimal) -> None:
    if not key:
        return
    bucket[key] = bucket.get(key, Decimal("0")) + amount


def money_q(value: Decimal) -> Decimal:
    return value.quantize(MONEY_Q)


def _is_zero_money(value: Decimal) -> bool:
    return money_q(value) == _money_zero()


def _anomaly(
    *,
    code: str,
    message: str,
    source_type: Optional[str] = None,
    source_id: Optional[str] = None,
    journal_id: Optional[str] = None,
    receipt_id: Optional[str] = None,
    movement_id: Optional[str] = None,
    acquisition_key: Optional[str] = None,
    account_id: Optional[str] = None,
    item_key: Optional[str] = None,
    blocks_reconciled: bool = True,
) -> dict:
    return {
        "code": code,
        "source_type": source_type,
        "source_id": source_id,
        "journal_id": journal_id,
        "receipt_id": receipt_id,
        "movement_id": movement_id,
        "acquisition_key": acquisition_key,
        "account_id": account_id,
        "item_key": item_key,
        "message": message,
        "blocks_reconciled": blocks_reconciled,
    }


def _anomaly_sort_key(row: dict) -> Tuple[str, ...]:
    return (
        _text(row.get("code")),
        _text(row.get("source_type")),
        _text(row.get("source_id")),
        _text(row.get("journal_id")),
        _text(row.get("receipt_id")),
        _text(row.get("movement_id")),
        _text(row.get("acquisition_key")),
        _text(row.get("account_id")),
        _text(row.get("item_key")),
        _text(row.get("message")),
    )


def _dedupe_anomalies(rows: Iterable[dict]) -> List[dict]:
    seen = set()
    out: List[dict] = []
    for row in rows or []:
        key = _anomaly_sort_key(row)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    out.sort(key=_anomaly_sort_key)
    return out


def _journal_fingerprint(journals: Sequence[dict]) -> Tuple[Tuple[str, ...], ...]:
    rows = []
    for doc in journals or []:
        rows.append((
            _text(doc.get("id")),
            _text(doc.get("status")),
            _text(doc.get("source_type")),
            _text(doc.get("source_id")),
            _text(doc.get("reversal_of")),
        ))
    return tuple(sorted(rows))


def _claim_lease_is_active(doc: Optional[dict], token_field: str, at_field: str, ttl: int) -> bool:
    token = _text((doc or {}).get(token_field))
    if not token:
        return False
    text = _text((doc or {}).get(at_field))
    if not text:
        return False
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return (now - parsed.astimezone(timezone.utc)).total_seconds() < ttl


def _issue_claim_is_active(doc: Optional[dict]) -> bool:
    return _claim_lease_is_active(
        doc, ISSUE_CLAIM_FIELD, ISSUE_CLAIM_AT_FIELD, ACCOUNTING_CLAIM_TTL_SECONDS
    )


async def _cursor_docs(cursor) -> List[dict]:
    rows: List[dict] = []
    batch: List[dict] = []
    async for doc in cursor:
        batch.append(doc)
        if len(batch) >= ACCOUNT_READ_BATCH_SIZE:
            rows.extend(batch)
            batch = []
    if batch:
        rows.extend(batch)
    return rows


async def _load_all_journals(db) -> List[dict]:
    cursor = db[JOURNAL_ENTRIES].find({}, JOURNAL_HEADER_PROJECTION)
    return await _cursor_docs(cursor)


async def _load_lines_for_journals(db, journal_ids: Sequence[str]) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {jid: [] for jid in journal_ids}
    ids = [jid for jid in journal_ids if jid]
    batch_size = JOURNAL_ID_BATCH_SIZE
    for i in range(0, len(ids), batch_size):
        chunk = ids[i:i + batch_size]
        cursor = db[JOURNAL_LINES].find(
            {"journal_id": {"$in": chunk}},
            JOURNAL_LINE_PROJECTION,
        )
        async for ln in cursor:
            jid = _text(ln.get("journal_id"))
            if jid in out:
                out[jid].append(ln)
            elif jid:
                out.setdefault(jid, []).append(ln)
    return out


async def _find_by_ids(
    db,
    collection: str,
    field: str,
    ids: Sequence[str],
    projection: Optional[dict] = None,
) -> Dict[str, dict]:
    wanted = [_text(i) for i in ids if _text(i)]
    out: Dict[str, dict] = {}
    proj = projection if projection is not None else {"_id": 0}
    batch_size = DOCUMENT_ID_BATCH_SIZE
    for i in range(0, len(wanted), batch_size):
        chunk = wanted[i:i + batch_size]
        cursor = db[collection].find({field: {"$in": chunk}}, proj)
        async for doc in cursor:
            key = _text(doc.get(field))
            if key:
                out[key] = doc
    return out


async def _load_stock_states(db, master_item_ids: Sequence[str]) -> Dict[str, dict]:
    wanted = [_text(i) for i in master_item_ids if _text(i)]
    out: Dict[str, dict] = {}
    batch_size = DOCUMENT_ID_BATCH_SIZE
    for i in range(0, len(wanted), batch_size):
        chunk = wanted[i:i + batch_size]
        cursor = db[STOCK_COLLECTION].find(
            {"$or": [{"master_item_id": {"$in": chunk}}, {"_id": {"$in": chunk}}]},
            {"_id": 1, "master_item_id": 1, "initialized": 1, "status": 1,
             "base_uom": 1, "total_qty": 1, "unprinted_qty": 1, "printed_qty": 1,
             "pending_event": 1},
        )
        async for doc in cursor:
            mid = _text(doc.get("master_item_id") or doc.get("_id"))
            if mid:
                out[mid] = doc
    return out


async def _load_all_receipts(db) -> List[dict]:
    cursor = db[GR_COLLECTION].find({}, {"_id": 0})
    return await _cursor_docs(cursor)


async def _load_all_issue_docs(db) -> List[dict]:
    cursor = db[ISSUE_COLLECTION].find({}, {"_id": 0})
    return await _cursor_docs(cursor)


async def _load_all_locks(db) -> List[dict]:
    cursor = db[LOCKS_COLLECTION].find({}, {"_id": 0})
    return await _cursor_docs(cursor)


def _signed_lines(lines: Sequence[dict]) -> Tuple[Dict[str, Decimal], List[dict]]:
    totals: Dict[str, Decimal] = {}
    anomalies: List[dict] = []
    for ln in lines or []:
        aid = _text(ln.get("account_id"))
        if not aid:
            anomalies.append(_anomaly(
                code=CODE_MISSING_JOURNAL_ACCOUNT,
                message="Baris jurnal tidak memiliki account_id.",
                journal_id=_text(ln.get("journal_id")) or None,
            ))
            continue
        try:
            debit = parse_money(ln.get("debit") if ln.get("debit") not in (None, "") else 0, field="debit")
            credit = parse_money(ln.get("credit") if ln.get("credit") not in (None, "") else 0, field="credit")
        except InvalidMoneyError as exc:
            anomalies.append(_anomaly(
                code=CODE_INVALID_MONETARY_VALUE,
                message=str(exc),
                journal_id=_text(ln.get("journal_id")) or None,
                account_id=aid,
            ))
            continue
        _add_money(totals, aid, debit - credit)
    return totals, anomalies


def _account_type_map(accounts: Sequence[dict]) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    for acc in accounts or []:
        public = acc if acc.get("id") else public_account(acc)
        if not public:
            continue
        aid = _text(public.get("id"))
        if aid:
            out[aid] = public
    return out


INCOMPLETE_CODES = frozenset({
    CODE_POSTED_JOURNAL_MISSING_DOCUMENT,
    CODE_POSTED_JOURNAL_MISSING_BASIS,
    CODE_INVALID_ACQUISITION_BASIS,
    CODE_CONSUMED_ACQUISITION_MISSING,
    CODE_CONSUMED_ACQUISITION_NOT_LIVE,
    CODE_MISSING_COA,
    CODE_INVALID_MONETARY_VALUE,
    CODE_MISSING_JOURNAL_ACCOUNT,
    CODE_INCOMPLETE_REVERSAL_PAIR,
    CODE_UNRESOLVED_ACCOUNT,
    CODE_DOCUMENT_POSTED_WITHOUT_JOURNAL,
})
DIFFERENCE_CODES = frozenset({
    CODE_BASIS_JOURNAL_MISMATCH,
    CODE_INCONSISTENT_REVERSAL_PAIR,
    CODE_NEGATIVE_REMAINING_FINANCIAL,
    CODE_INVALID_ACCOUNT_CLASSIFICATION,
})


def _anomaly_effects(anomalies: Sequence[dict]) -> Tuple[bool, bool]:
    incomplete = False
    difference = False
    for row in anomalies or []:
        if row.get("blocks_reconciled") is False:
            continue
        code = _text(row.get("code"))
        if code in INCOMPLETE_CODES:
            incomplete = True
        elif code in DIFFERENCE_CODES:
            difference = True
        else:
            incomplete = True
    return incomplete, difference


def _value_status(
    *,
    basis: Decimal,
    source: Decimal,
    gl: Decimal,
    coverage_complete: bool,
    incomplete_anomalies: bool,
    difference_anomalies: bool,
    unstable: bool,
) -> str:
    if unstable or not coverage_complete or incomplete_anomalies:
        return STATUS_INCOMPLETE
    basis_vs_source = money_q(source - basis)
    other = money_q(gl - source)
    if difference_anomalies or not _is_zero_money(basis_vs_source):
        return STATUS_DIFFERENCE
    if not _is_zero_money(other):
        return STATUS_EXPLAINED_DIFFERENCE
    return STATUS_RECONCILED


def _combine_status(statuses: Sequence[str]) -> str:
    if any(s == STATUS_INCOMPLETE for s in statuses):
        return STATUS_INCOMPLETE
    if any(s == STATUS_DIFFERENCE for s in statuses):
        return STATUS_DIFFERENCE
    if any(s == STATUS_EXPLAINED_DIFFERENCE for s in statuses):
        return STATUS_EXPLAINED_DIFFERENCE
    return STATUS_RECONCILED


def _ref(kind: str, ident: str, extra: Optional[dict] = None) -> dict:
    row = {"kind": kind, "id": ident}
    if extra:
        row.update(extra)
    return row


def _pending_bucket(items: Sequence[dict]) -> dict:
    refs = list(items)
    refs.sort(key=lambda r: (_text(r.get("kind")), _text(r.get("id"))))
    return {"count": len(refs), "items": refs}


def _live_primary(journal: Optional[dict]) -> bool:
    if not journal:
        return False
    if _text(journal.get("status")) != STATUS_POSTED:
        return False
    if journal.get("reversal_of") not in (None, ""):
        return False
    return True


def _parse_acquisition_line(
    receipt: dict,
    basis: dict,
    line: dict,
    *,
    journal: dict,
    anomalies: List[dict],
) -> Optional[dict]:
    receipt_id = _text(basis.get("receipt_id") or receipt.get("id"))
    line_id = _text(line.get("line_id"))
    item_key = _text(line.get("financial_inventory_item_key"))
    master_item_id = _text(line.get("master_item_id"))
    if not item_key and master_item_id:
        item_key = financial_inventory_item_key({"master_item_id": master_item_id})
    inv_account = _text(basis.get("inventory_account_id"))
    journal_id = _text(journal.get("id"))
    if not receipt_id or not line_id:
        anomalies.append(_anomaly(
            code=CODE_INVALID_ACQUISITION_BASIS,
            message="Baris akuisisi GR tidak memiliki receipt_id/line_id.",
            source_type=SOURCE_GR,
            source_id=receipt_id or _text(journal.get("source_id")) or None,
            journal_id=journal_id or None,
            receipt_id=receipt_id or None,
            item_key=item_key or None,
        ))
        return None
    if not item_key or not inv_account:
        anomalies.append(_anomaly(
            code=CODE_INVALID_ACQUISITION_BASIS,
            message="Baris akuisisi GR kehilangan financial item key atau akun Persediaan.",
            source_type=SOURCE_GR,
            source_id=receipt_id,
            journal_id=journal_id or None,
            receipt_id=receipt_id,
            acquisition_key=acquisition_key(receipt_id, line_id),
            account_id=inv_account or None,
            item_key=item_key or None,
        ))
        return None
    try:
        base_qty = parse_qty(line.get("base_qty"), field="base_qty")
        total_cost = parse_money(
            line.get("total_cost") if line.get("total_cost") not in (None, "")
            else line.get("line_financial_amount"),
            field="total_cost",
        )
    except (InvalidQtyError, InvalidMoneyError) as exc:
        anomalies.append(_anomaly(
            code=CODE_INVALID_MONETARY_VALUE,
            message=f"Nilai akuisisi GR tidak valid: {exc}",
            source_type=SOURCE_GR,
            source_id=receipt_id,
            journal_id=journal_id or None,
            receipt_id=receipt_id,
            acquisition_key=acquisition_key(receipt_id, line_id),
            account_id=inv_account,
            item_key=item_key,
        ))
        return None
    if base_qty <= 0:
        anomalies.append(_anomaly(
            code=CODE_INVALID_ACQUISITION_BASIS,
            message="Qty akuisisi GR harus lebih besar dari 0.",
            source_type=SOURCE_GR,
            source_id=receipt_id,
            journal_id=journal_id or None,
            receipt_id=receipt_id,
            acquisition_key=acquisition_key(receipt_id, line_id),
            account_id=inv_account,
            item_key=item_key,
        ))
        return None
    return {
        "acquisition_key": acquisition_key(receipt_id, line_id),
        "receipt_id": receipt_id,
        "line_id": line_id,
        "financial_inventory_item_key": item_key,
        "master_item_id": master_item_id,
        "base_qty": base_qty,
        "base_uom": _text(line.get("base_uom")),
        "total_cost": total_cost,
        "inventory_account_id": inv_account,
        "journal_id": journal_id,
    }


def _expected_issue_inventory_credits(basis: dict, anomalies: List[dict], journal: dict) -> Dict[str, Decimal]:
    method = parse_financial_hpp_method(basis.get("hpp_method"))
    credits: Dict[str, Decimal] = {}
    movement_id = _text(basis.get("movement_id") or journal.get("source_id"))
    journal_id = _text(journal.get("id"))
    if method == METHOD_FIFO:
        consumed_rows = _as_dict_list(basis.get("consumed_acquisitions"))
        if consumed_rows is None:
            anomalies.append(_anomaly(
                code=CODE_INVALID_ACQUISITION_BASIS,
                message="Basis FIFO memiliki consumed_acquisitions yang tidak valid.",
                source_type=SOURCE_ISSUE,
                source_id=movement_id or None,
                journal_id=journal_id or None,
                movement_id=movement_id or None,
            ))
            return credits
        for consumed in consumed_rows:
            aid = _text(consumed.get("inventory_account_id"))
            try:
                amount = parse_money(consumed.get("total_cost"), field="consumed.total_cost")
            except InvalidMoneyError as exc:
                anomalies.append(_anomaly(
                    code=CODE_INVALID_MONETARY_VALUE,
                    message=str(exc),
                    source_type=SOURCE_ISSUE,
                    source_id=movement_id or None,
                    journal_id=journal_id or None,
                    movement_id=movement_id or None,
                    acquisition_key=_text(consumed.get("acquisition_key")) or None,
                    account_id=aid or None,
                ))
                continue
            if not aid:
                anomalies.append(_anomaly(
                    code=CODE_INVALID_ACQUISITION_BASIS,
                    message="Konsumsi FIFO tidak memiliki inventory_account_id.",
                    source_type=SOURCE_ISSUE,
                    source_id=movement_id or None,
                    journal_id=journal_id or None,
                    movement_id=movement_id or None,
                    acquisition_key=_text(consumed.get("acquisition_key")) or None,
                ))
                continue
            _add_money(credits, aid, amount)
        return credits
    aid = _text(basis.get("inventory_account_id"))
    try:
        amount = parse_money(basis.get("total_cogs"), field="total_cogs")
    except InvalidMoneyError as exc:
        anomalies.append(_anomaly(
            code=CODE_INVALID_MONETARY_VALUE,
            message=str(exc),
            source_type=SOURCE_ISSUE,
            source_id=movement_id or None,
            journal_id=journal_id or None,
            movement_id=movement_id or None,
            account_id=aid or None,
        ))
        return credits
    if not aid:
        anomalies.append(_anomaly(
            code=CODE_INVALID_ACQUISITION_BASIS,
            message="Basis RATA-RATA tidak memiliki inventory_account_id tersimpan.",
            source_type=SOURCE_ISSUE,
            source_id=movement_id or None,
            journal_id=journal_id or None,
            movement_id=movement_id or None,
        ))
        return credits
    _add_money(credits, aid, amount)
    return credits


def _relevant_signed_for_compare(
    journal_signed: Dict[str, Decimal],
    expected: Dict[str, Decimal],
    *,
    coa: Dict[str, dict],
    historical_inventory: set,
    historical_cogs: set,
) -> Dict[str, Decimal]:
    """Actual Inventory/COGS signed lines plus every expected key.

    Legitimate pair accounts (clearing/AP and other non-inventory types) stay
    out of the per-source inventory compare unless they are themselves expected.
    """
    actual: Dict[str, Decimal] = {}
    for aid in set(expected) | set(journal_signed):
        if not aid:
            continue
        if aid in expected:
            actual[aid] = money_q(journal_signed.get(aid, _money_zero()))
            continue
        kind = _classify_inventory_account(
            aid,
            coa=coa,
            historical_inventory=historical_inventory,
            historical_cogs=historical_cogs,
        )
        if kind in {"inventory", "cogs"}:
            actual[aid] = money_q(journal_signed.get(aid, _money_zero()))
    return actual


def _compare_basis_to_journal(
    *,
    expected: Dict[str, Decimal],
    actual: Dict[str, Decimal],
    source_type: str,
    source_id: str,
    journal_id: str,
    receipt_id: Optional[str] = None,
    movement_id: Optional[str] = None,
) -> List[dict]:
    anomalies: List[dict] = []
    keys = sorted(set(expected) | set(actual))
    for aid in keys:
        exp = money_q(expected.get(aid, _money_zero()))
        act = money_q(actual.get(aid, _money_zero()))
        if exp == act:
            continue
        anomalies.append(_anomaly(
            code=CODE_BASIS_JOURNAL_MISMATCH,
            message=(
                f"Basis {exp} berbeda dari jurnal {act} pada akun {aid}."
            ),
            source_type=source_type,
            source_id=source_id,
            journal_id=journal_id or None,
            receipt_id=receipt_id,
            movement_id=movement_id,
            account_id=aid,
        ))
    return anomalies


def _reversal_anomalies(journals: Sequence[dict], lines_by_id: Dict[str, List[dict]]) -> List[dict]:
    by_id = {_text(j.get("id")): j for j in journals if _text(j.get("id"))}
    reversals_by_original: Dict[str, List[dict]] = {}
    for journal in journals:
        target = _text(journal.get("reversal_of"))
        if target:
            reversals_by_original.setdefault(target, []).append(journal)
    anomalies: List[dict] = []
    family = [j for j in journals if _text(j.get("source_type")) in INVENTORY_FAMILY]
    for journal in family:
        jid = _text(journal.get("id"))
        status = _text(journal.get("status"))
        source_type = _text(journal.get("source_type"))
        source_id = _text(journal.get("source_id"))
        reversal_of = _text(journal.get("reversal_of"))
        if status == STATUS_REVERSED and not reversal_of:
            revs = reversals_by_original.get(jid) or []
            if not revs:
                anomalies.append(_anomaly(
                    code=CODE_INCOMPLETE_REVERSAL_PAIR,
                    message="Jurnal original berstatus reversed tanpa jurnal reversal.",
                    source_type=source_type or None,
                    source_id=source_id or None,
                    journal_id=jid or None,
                    receipt_id=source_id if source_type in {SOURCE_GR, SOURCE_GR_REVERSAL} else None,
                    movement_id=source_id if source_type == SOURCE_ISSUE else None,
                ))
        if reversal_of and reversal_of not in by_id:
            anomalies.append(_anomaly(
                code=CODE_INCOMPLETE_REVERSAL_PAIR,
                message="Jurnal reversal menunjuk original yang tidak ditemukan.",
                source_type=source_type or None,
                source_id=source_id or None,
                journal_id=jid or None,
                receipt_id=source_id if source_type in {SOURCE_GR, SOURCE_GR_REVERSAL} else None,
                movement_id=source_id if source_type == SOURCE_ISSUE else None,
            ))
        if reversal_of and reversal_of in by_id:
            original = by_id[reversal_of]
            if _text(original.get("status")) == STATUS_POSTED:
                anomalies.append(_anomaly(
                    code=CODE_INCONSISTENT_REVERSAL_PAIR,
                    message="Jurnal reversal ada tetapi original masih posted.",
                    source_type=source_type or None,
                    source_id=source_id or None,
                    journal_id=jid or None,
                    receipt_id=source_id if source_type in {SOURCE_GR, SOURCE_GR_REVERSAL} else None,
                    movement_id=source_id if source_type == SOURCE_ISSUE else None,
                ))
            orig_signed, _ = _signed_lines(lines_by_id.get(reversal_of) or [])
            rev_signed, _ = _signed_lines(lines_by_id.get(jid) or [])
            accounts = set(orig_signed) | set(rev_signed)
            for aid in accounts:
                if money_q(orig_signed.get(aid, _money_zero()) + rev_signed.get(aid, _money_zero())) != _money_zero():
                    anomalies.append(_anomaly(
                        code=CODE_INCONSISTENT_REVERSAL_PAIR,
                        message=f"Pasangan reversal tidak net-zero pada akun {aid}.",
                        source_type=source_type or None,
                        source_id=source_id or None,
                        journal_id=jid or None,
                        account_id=aid,
                        receipt_id=source_id if source_type in {SOURCE_GR, SOURCE_GR_REVERSAL} else None,
                        movement_id=source_id if source_type == SOURCE_ISSUE else None,
                    ))
    return anomalies


def _classify_inventory_account(
    account_id: str,
    *,
    coa: Dict[str, dict],
    historical_inventory: set,
    historical_cogs: set,
) -> str:
    if account_id in historical_inventory:
        return "inventory"
    if account_id in historical_cogs:
        return "cogs"
    acc = coa.get(account_id)
    if not acc:
        return "unresolved"
    atype = _text(acc.get("account_type")).upper()
    if atype == ACCOUNT_TYPE_INVENTORY:
        return "inventory"
    if atype == ACCOUNT_TYPE_COGS:
        return "cogs"
    return "other"


def _account_payload(account_id: str, coa: Dict[str, dict], kind: str) -> dict:
    acc = coa.get(account_id) or {}
    return {
        "account_id": account_id,
        "code": acc.get("code"),
        "name": acc.get("name"),
        "account_type": acc.get("account_type"),
        "classification": kind,
        "coa_resolved": bool(acc),
    }


def _reconciliation_triplet(basis: Decimal, source: Decimal, gl: Decimal) -> dict:
    basis = money_q(basis)
    source = money_q(source)
    gl = money_q(gl)
    basis_vs_source = money_q(source - basis)
    other = money_q(gl - source)
    gl_vs_basis = money_q(gl - basis)
    return {
        "financial_basis_value": money_float(basis),
        "source_journal_balance": money_float(source),
        "gl_balance": money_float(gl),
        "basis_vs_source_difference": money_float(basis_vs_source),
        "other_journal_balance": money_float(other),
        "gl_vs_basis_difference": money_float(gl_vs_basis),
        "identity_holds": gl_vs_basis == money_q(basis_vs_source + other),
    }


async def build_inventory_reconciliation_context(db) -> dict:
    """Shared in-request read. Summary/accounts/items use this same source set."""
    generated_at = now_iso()
    settings = await get_purchase_accounting_settings(db)
    effective_date = settings.get("inventory_accounting_effective_date")
    accounts = await load_statement_accounts(db)
    coa = _account_type_map(accounts)

    journals_first = await _load_all_journals(db)
    fingerprint_before = _journal_fingerprint(journals_first)
    journal_ids = [_text(j.get("id")) for j in journals_first if _text(j.get("id"))]
    lines_by_id = await _load_lines_for_journals(db, journal_ids)

    receipts = await _load_all_receipts(db)
    receipts_by_id = {_text(r.get("id")): r for r in receipts if _text(r.get("id"))}
    issue_docs = await _load_all_issue_docs(db)
    issues_by_movement = {
        _text(r.get("movement_id")): r for r in issue_docs if _text(r.get("movement_id"))
    }
    locks = await _load_all_locks(db)

    anomalies: List[dict] = []
    posting_flags: List[str] = []
    unstable = False

    for receipt in receipts:
        if posting_claim_is_active(receipt) or reverse_claim_is_active(receipt) or cancel_claim_is_active(receipt):
            unstable = True
            break
    if not unstable:
        for issue in issue_docs:
            if _issue_claim_is_active(issue):
                unstable = True
                break
    if not unstable:
        for lock in locks:
            if lock_is_active(lock):
                unstable = True
                break

    legitimate: List[dict] = []
    for journal in journals_first:
        jid = _text(journal.get("id"))
        if not jid:
            continue
        if not journal_is_legitimate(journal):
            continue
        journal = dict(journal)
        journal["lines"] = lines_by_id.get(jid) or []
        legitimate.append(journal)

    anomalies.extend(_reversal_anomalies(legitimate, lines_by_id))

    live_gr_journals: Dict[str, dict] = {}
    live_issue_journals: Dict[str, dict] = {}
    family_journals: List[dict] = []
    all_signed_by_account: Dict[str, Decimal] = {}
    family_signed_by_account: Dict[str, Decimal] = {}
    line_anomalies: List[dict] = []

    for journal in legitimate:
        signed, line_issues = _signed_lines(journal.get("lines") or [])
        for issue in line_issues:
            issue["source_type"] = _text(journal.get("source_type")) or issue.get("source_type")
            issue["source_id"] = _text(journal.get("source_id")) or issue.get("source_id")
            issue["journal_id"] = _text(journal.get("id")) or issue.get("journal_id")
            line_anomalies.append(issue)
        for aid, amount in signed.items():
            _add_money(all_signed_by_account, aid, amount)
        source_type = _text(journal.get("source_type"))
        source_id = _text(journal.get("source_id"))
        if source_type in INVENTORY_FAMILY:
            family_journals.append(journal)
            for aid, amount in signed.items():
                _add_money(family_signed_by_account, aid, amount)
        if source_type == SOURCE_GR and _live_primary(journal) and source_id:
            live_gr_journals[source_id] = journal
        if source_type == SOURCE_ISSUE and _live_primary(journal) and source_id:
            live_issue_journals[source_id] = journal
    anomalies.extend(line_anomalies)

    historical_inventory: set = set()
    historical_cogs: set = set()
    acquisitions: Dict[str, dict] = {}
    item_acq_qty: Dict[str, Decimal] = {}
    item_issue_qty: Dict[str, Decimal] = {}
    item_uoms: Dict[str, set] = {}
    item_master: Dict[str, str] = {}
    item_keys_order: List[str] = []
    inventory_basis: Dict[str, Decimal] = {}
    cogs_basis: Dict[str, Decimal] = {}
    inventory_source: Dict[str, Decimal] = {}
    cogs_source: Dict[str, Decimal] = {}
    coverage_complete = True
    rata_rata_used = False
    fifo_used = False
    rejected_gr_ids: set = set()
    rejected_issue_ids: set = set()

    # Recognize live GR acquisitions from journals first.
    for source_id, journal in sorted(live_gr_journals.items()):
        receipt = receipts_by_id.get(source_id)
        journal_id = _text(journal.get("id"))
        if not receipt:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_POSTED_JOURNAL_MISSING_DOCUMENT,
                message="Jurnal GR posted ada tetapi dokumen penerimaan tidak ditemukan.",
                source_type=SOURCE_GR,
                source_id=source_id,
                journal_id=journal_id or None,
                receipt_id=source_id,
            ))
            continue
        basis = receipt.get(GR_BASIS_FIELD) or {}
        if not isinstance(basis, dict) or not basis:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_POSTED_JOURNAL_MISSING_BASIS,
                message="Jurnal GR posted ada tetapi accounting_posting_basis tidak valid.",
                source_type=SOURCE_GR,
                source_id=source_id,
                journal_id=journal_id or None,
                receipt_id=source_id,
            ))
            continue
        inv_account = _text(basis.get("inventory_account_id"))
        if inv_account:
            historical_inventory.add(inv_account)
        eligible_lines = _as_dict_list(basis.get("eligible_inventory_lines"))
        if eligible_lines is None:
            coverage_complete = False
            rejected_gr_ids.add(source_id)
            anomalies.append(_anomaly(
                code=CODE_INVALID_ACQUISITION_BASIS,
                message="Basis GR memiliki eligible_inventory_lines yang tidak valid.",
                source_type=SOURCE_GR,
                source_id=source_id,
                journal_id=journal_id or None,
                receipt_id=source_id,
            ))
            continue
        recognized_lines: List[dict] = []
        expected_inventory = _money_zero()
        for line in eligible_lines:
            acq = _parse_acquisition_line(
                receipt, basis, line, journal=journal, anomalies=anomalies
            )
            if not acq:
                coverage_complete = False
                continue
            recognized_lines.append(acq)
            acquisitions[acq["acquisition_key"]] = acq
            expected_inventory = money_q(expected_inventory + acq["total_cost"])
            _add_money(inventory_basis, acq["inventory_account_id"], acq["total_cost"])
            item_key = acq["financial_inventory_item_key"]
            if item_key not in item_keys_order:
                item_keys_order.append(item_key)
            item_master[item_key] = acq["master_item_id"] or item_master.get(item_key, "")
            _add_qty(item_acq_qty, item_key, acq["base_qty"])
            if acq["base_uom"]:
                item_uoms.setdefault(item_key, set()).add(_normalize_uom(acq["base_uom"]))
        journal_signed, _ = _signed_lines(journal.get("lines") or [])
        if inv_account:
            expected = {inv_account: expected_inventory}
            actual = _relevant_signed_for_compare(
                journal_signed,
                expected,
                coa=coa,
                historical_inventory=historical_inventory,
                historical_cogs=historical_cogs,
            )
            anomalies.extend(_compare_basis_to_journal(
                expected=expected,
                actual=actual,
                source_type=SOURCE_GR,
                source_id=source_id,
                journal_id=journal_id,
                receipt_id=source_id,
            ))
        elif recognized_lines:
            coverage_complete = False

    # Recognize live issue consumption from journals first.
    for source_id, journal in sorted(live_issue_journals.items()):
        doc = issues_by_movement.get(source_id)
        journal_id = _text(journal.get("id"))
        if not doc:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_POSTED_JOURNAL_MISSING_DOCUMENT,
                message="Jurnal inventory_issue posted ada tetapi dokumen accounting tidak ditemukan.",
                source_type=SOURCE_ISSUE,
                source_id=source_id,
                journal_id=journal_id or None,
                movement_id=source_id,
            ))
            continue
        basis = doc.get(POSTING_BASIS_FIELD) or {}
        if not isinstance(basis, dict) or not basis:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_POSTED_JOURNAL_MISSING_BASIS,
                message="Jurnal inventory_issue posted ada tetapi accounting_posting_basis tidak valid.",
                source_type=SOURCE_ISSUE,
                source_id=source_id,
                journal_id=journal_id or None,
                movement_id=source_id,
            ))
            continue
        method = parse_financial_hpp_method(basis.get("hpp_method"))
        if method == METHOD_FIFO:
            consumed_rows = _as_dict_list(basis.get("consumed_acquisitions"))
            if consumed_rows is None:
                coverage_complete = False
                rejected_issue_ids.add(source_id)
                anomalies.append(_anomaly(
                    code=CODE_INVALID_ACQUISITION_BASIS,
                    message="Basis FIFO memiliki consumed_acquisitions yang tidak valid.",
                    source_type=SOURCE_ISSUE,
                    source_id=source_id,
                    journal_id=journal_id or None,
                    movement_id=source_id,
                ))
                continue
        item_key = _text(basis.get("financial_inventory_item_key") or doc.get("financial_inventory_item_key"))
        if item_key and item_key not in item_keys_order:
            item_keys_order.append(item_key)
        if item_key and item_key.startswith("m:"):
            item_master.setdefault(item_key, item_key[2:])
        cogs_account = _text(basis.get("cogs_account_id"))
        if cogs_account:
            historical_cogs.add(cogs_account)
        try:
            total_cogs = parse_money(basis.get("total_cogs"), field="total_cogs")
        except InvalidMoneyError as exc:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_INVALID_MONETARY_VALUE,
                message=str(exc),
                source_type=SOURCE_ISSUE,
                source_id=source_id,
                journal_id=journal_id or None,
                movement_id=source_id,
                account_id=cogs_account or None,
                item_key=item_key or None,
            ))
            total_cogs = None
        if total_cogs is not None and cogs_account:
            _add_money(cogs_basis, cogs_account, total_cogs)

        credits = _expected_issue_inventory_credits(basis, anomalies, journal)
        for aid, amount in credits.items():
            historical_inventory.add(aid)
            _add_money(inventory_basis, aid, -amount)

        journal_signed, _ = _signed_lines(journal.get("lines") or [])
        expected_compare = dict(credits)
        if cogs_account and total_cogs is not None:
            # Journal COGS is a debit (+) while inventory credits are stored positive then negated in B.
            expected_compare[cogs_account] = total_cogs
        # Inventory credits on the journal are negative signed balances.
        for aid in credits:
            expected_compare[aid] = money_q(-credits[aid])
        actual_compare = _relevant_signed_for_compare(
            journal_signed,
            expected_compare,
            coa=coa,
            historical_inventory=historical_inventory,
            historical_cogs=historical_cogs,
        )
        anomalies.extend(_compare_basis_to_journal(
            expected=expected_compare,
            actual=actual_compare,
            source_type=SOURCE_ISSUE,
            source_id=source_id,
            journal_id=journal_id,
            movement_id=source_id,
        ))

        if method == METHOD_FIFO:
            fifo_used = True
            consumed_qty = Decimal("0")
            consumed_uom = _text(basis.get("base_uom"))
            for consumed in consumed_rows:
                key = _text(consumed.get("acquisition_key"))
                if not key:
                    coverage_complete = False
                    anomalies.append(_anomaly(
                        code=CODE_CONSUMED_ACQUISITION_MISSING,
                        message="Konsumsi FIFO tidak memiliki acquisition_key.",
                        source_type=SOURCE_ISSUE,
                        source_id=source_id,
                        journal_id=journal_id or None,
                        movement_id=source_id,
                        item_key=item_key or None,
                    ))
                    continue
                if key not in acquisitions:
                    coverage_complete = False
                    anomalies.append(_anomaly(
                        code=CODE_CONSUMED_ACQUISITION_NOT_LIVE,
                        message="Konsumsi FIFO merujuk akuisisi yang jurnal GR-nya tidak lagi live.",
                        source_type=SOURCE_ISSUE,
                        source_id=source_id,
                        journal_id=journal_id or None,
                        movement_id=source_id,
                        acquisition_key=key,
                        item_key=item_key or None,
                    ))
                try:
                    qty = parse_qty(consumed.get("base_qty_consumed"), field="base_qty_consumed")
                except InvalidQtyError as exc:
                    coverage_complete = False
                    anomalies.append(_anomaly(
                        code=CODE_INVALID_MONETARY_VALUE,
                        message=str(exc),
                        source_type=SOURCE_ISSUE,
                        source_id=source_id,
                        journal_id=journal_id or None,
                        movement_id=source_id,
                        acquisition_key=key,
                        item_key=item_key or None,
                    ))
                    continue
                consumed_qty += qty
                uom = _text(consumed.get("base_uom")) or consumed_uom
                if item_key and uom:
                    item_uoms.setdefault(item_key, set()).add(_normalize_uom(uom))
            if item_key:
                _add_qty(item_issue_qty, item_key, consumed_qty)
        elif method == METHOD_AVG:
            rata_rata_used = True
            try:
                qty = parse_qty(
                    basis.get("issue_base_qty") if basis.get("issue_base_qty") not in (None, "")
                    else basis.get("base_qty"),
                    field="issue_base_qty",
                )
            except InvalidQtyError as exc:
                coverage_complete = False
                anomalies.append(_anomaly(
                    code=CODE_INVALID_MONETARY_VALUE,
                    message=str(exc),
                    source_type=SOURCE_ISSUE,
                    source_id=source_id,
                    journal_id=journal_id or None,
                    movement_id=source_id,
                    item_key=item_key or None,
                ))
                qty = None
            if item_key and qty is not None:
                _add_qty(item_issue_qty, item_key, qty)
            uom = _text(basis.get("base_uom"))
            if item_key and uom:
                item_uoms.setdefault(item_key, set()).add(_normalize_uom(uom))
        else:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_POSTED_JOURNAL_MISSING_BASIS,
                message="Basis inventory_issue tidak memiliki metode FIFO/RATA-RATA yang dikenali.",
                source_type=SOURCE_ISSUE,
                source_id=source_id,
                journal_id=journal_id or None,
                movement_id=source_id,
                item_key=item_key or None,
            ))

    remaining_qty_by_acq: Dict[str, Decimal] = {}
    remaining_value_by_acq: Dict[str, Decimal] = {}
    for key, acq in acquisitions.items():
        remaining_qty_by_acq[key] = acq["base_qty"]
        remaining_value_by_acq[key] = acq["total_cost"]
    for source_id, journal in sorted(live_issue_journals.items()):
        if source_id in rejected_issue_ids:
            continue
        doc = issues_by_movement.get(source_id) or {}
        basis = doc.get(POSTING_BASIS_FIELD) or {}
        if not isinstance(basis, dict):
            continue
        if parse_financial_hpp_method(basis.get("hpp_method")) != METHOD_FIFO:
            continue
        consumed_rows = _as_dict_list(basis.get("consumed_acquisitions"))
        if consumed_rows is None:
            continue
        for consumed in consumed_rows:
            key = _text(consumed.get("acquisition_key"))
            if key not in remaining_qty_by_acq:
                continue
            try:
                qty = parse_qty(consumed.get("base_qty_consumed"), field="base_qty_consumed")
                value = parse_money(consumed.get("total_cost"), field="consumed.total_cost")
            except (InvalidQtyError, InvalidMoneyError):
                continue
            remaining_qty_by_acq[key] -= qty
            remaining_value_by_acq[key] = money_q(remaining_value_by_acq[key] - value)
    for key, qty in remaining_qty_by_acq.items():
        value = remaining_value_by_acq.get(key, _money_zero())
        if qty < 0 or value < 0:
            acq = acquisitions.get(key) or {}
            anomalies.append(_anomaly(
                code=CODE_NEGATIVE_REMAINING_FINANCIAL,
                message="Sisa qty/nilai finansial akuisisi menjadi negatif.",
                source_type=SOURCE_GR,
                source_id=acq.get("receipt_id"),
                journal_id=acq.get("journal_id"),
                receipt_id=acq.get("receipt_id"),
                acquisition_key=key,
                account_id=acq.get("inventory_account_id"),
                item_key=acq.get("financial_inventory_item_key"),
            ))

    # Document-says-posted but no live journal.
    for receipt in receipts:
        rid = _text(receipt.get("id"))
        if not rid or rid in live_gr_journals:
            continue
        status = _text(receipt.get("accounting_status")).lower()
        journal_ref = _text(receipt.get("accounting_journal_id"))
        if status == ACCOUNTING_POSTED or journal_ref:
            anomalies.append(_anomaly(
                code=CODE_DOCUMENT_POSTED_WITHOUT_JOURNAL,
                message="Dokumen GR menandai posted tetapi jurnal live tidak ditemukan.",
                source_type=SOURCE_GR,
                source_id=rid,
                journal_id=journal_ref or None,
                receipt_id=rid,
            ))
    for issue in issue_docs:
        mid = _text(issue.get("movement_id"))
        if not mid or mid in live_issue_journals:
            continue
        status = _text(issue.get("status")).lower()
        journal_ref = _text(issue.get("accounting_journal_id"))
        if status == STATUS_POSTED or journal_ref:
            anomalies.append(_anomaly(
                code=CODE_DOCUMENT_POSTED_WITHOUT_JOURNAL,
                message="Dokumen inventory_issue menandai posted tetapi jurnal live tidak ditemukan.",
                source_type=SOURCE_ISSUE,
                source_id=mid,
                journal_id=journal_ref or None,
                movement_id=mid,
            ))

    inventory_account_ids = {
        _text(acc.get("id"))
        for acc in coa.values()
        if _text(acc.get("account_type")).upper() == ACCOUNT_TYPE_INVENTORY
    }
    cogs_account_ids = {
        _text(acc.get("id"))
        for acc in coa.values()
        if _text(acc.get("account_type")).upper() == ACCOUNT_TYPE_COGS
    }
    inventory_account_ids |= set(historical_inventory)
    cogs_account_ids |= set(historical_cogs)
    unresolved_accounts: Dict[str, Decimal] = {}

    for aid, amount in family_signed_by_account.items():
        kind = _classify_inventory_account(
            aid,
            coa=coa,
            historical_inventory=historical_inventory,
            historical_cogs=historical_cogs,
        )
        if kind == "inventory":
            inventory_account_ids.add(aid)
            _add_money(inventory_source, aid, amount)
        elif kind == "cogs":
            # Only inventory_issue family lines belong in S_cogs. GR family should not hit COGS.
            cogs_account_ids.add(aid)
        elif kind == "unresolved":
            unresolved_accounts[aid] = money_q(unresolved_accounts.get(aid, _money_zero()) + amount)
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_UNRESOLVED_ACCOUNT,
                message="Jumlah jurnal keluarga persediaan berada di akun yang tidak dapat diklasifikasi.",
                account_id=aid,
            ))

    # S_cogs from inventory_issue journals only.
    for journal in family_journals:
        if _text(journal.get("source_type")) != SOURCE_ISSUE:
            continue
        signed, _ = _signed_lines(journal.get("lines") or [])
        for aid, amount in signed.items():
            kind = _classify_inventory_account(
                aid,
                coa=coa,
                historical_inventory=historical_inventory,
                historical_cogs=historical_cogs,
            )
            if kind == "cogs" or aid in cogs_account_ids:
                _add_money(cogs_source, aid, amount)
            elif kind == "unresolved":
                unresolved_accounts[aid] = money_q(unresolved_accounts.get(aid, _money_zero()) + amount)

    for aid in list(historical_inventory):
        acc = coa.get(aid)
        if not acc:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_MISSING_COA,
                message="Akun Persediaan historis tidak ada di COA.",
                account_id=aid,
            ))
        elif _text(acc.get("account_type")).upper() not in {ACCOUNT_TYPE_INVENTORY, ""}:
            if _text(acc.get("account_type")).upper() != ACCOUNT_TYPE_INVENTORY:
                anomalies.append(_anomaly(
                    code=CODE_INVALID_ACCOUNT_CLASSIFICATION,
                    message="Akun historis Persediaan tidak bertipe INTR pada COA saat ini.",
                    account_id=aid,
                ))
    for aid in list(historical_cogs):
        acc = coa.get(aid)
        if not acc:
            coverage_complete = False
            anomalies.append(_anomaly(
                code=CODE_MISSING_COA,
                message="Akun HPP historis tidak ada di COA.",
                account_id=aid,
            ))
        elif _text(acc.get("account_type")).upper() != ACCOUNT_TYPE_COGS:
            anomalies.append(_anomaly(
                code=CODE_INVALID_ACCOUNT_CLASSIFICATION,
                message="Akun historis HPP tidak bertipe COGS pada COA saat ini.",
                account_id=aid,
            ))

    inventory_gl = {aid: money_q(all_signed_by_account.get(aid, _money_zero())) for aid in inventory_account_ids}
    cogs_gl = {aid: money_q(all_signed_by_account.get(aid, _money_zero())) for aid in cogs_account_ids}
    for aid in inventory_account_ids:
        inventory_basis.setdefault(aid, _money_zero())
        inventory_source.setdefault(aid, _money_zero())
    for aid in cogs_account_ids:
        cogs_basis.setdefault(aid, _money_zero())
        cogs_source.setdefault(aid, _money_zero())

    # Pending / excluded coverage — bulk evaluation, never per-movement GET.
    # Invalid GR bases stay anomalies. The read-only evaluator isolates those
    # items instead of dropping every other issue.
    evaluations = await evaluate_inventory_issues(db)
    pending_gr: List[dict] = []
    pending_cogs: List[dict] = []
    waiting_cogs: List[dict] = []
    blocked_cogs: List[dict] = []
    pre_effective: List[dict] = []
    excluded_service: List[dict] = []
    recovery_refs: List[dict] = []

    unjournaled_receipts = [
        r for r in receipts
        if _text(r.get("id")) and _text(r.get("id")) not in live_gr_journals
    ]
    readiness_map = await readiness_for_receipts(db, unjournaled_receipts) if unjournaled_receipts else {}
    primary_by_receipt: Dict[str, dict] = {}
    for journal in journals_first:
        if _text(journal.get("source_type")) != SOURCE_GR:
            continue
        if journal.get("reversal_of") not in (None, ""):
            continue
        sid = _text(journal.get("source_id"))
        if sid:
            primary_by_receipt[sid] = journal

    for receipt in receipts:
        rid = _text(receipt.get("id"))
        if not rid:
            continue
        primary = primary_by_receipt.get(rid)
        reversed_primary = bool(primary and _text(primary.get("status")) == STATUS_REVERSED)
        recovering = recovery_required(receipt) or (reversed_primary and not is_cancelled(receipt))
        if recovering:
            recovery_refs.append(_ref("goods_receipt", rid, {"receipt_number": receipt.get("receipt_number")}))
            posting_flags.append(FLAG_RECOVERY_REQUIRED)
            # Financial acquisition is already gone. Keep stored item identity so
            # the physical leftover remains visible as a recovery quantity gap.
            if rid in rejected_gr_ids:
                continue
            basis = receipt.get(GR_BASIS_FIELD)
            if not isinstance(basis, dict):
                continue
            eligible_lines = _as_dict_list(basis.get("eligible_inventory_lines"))
            if eligible_lines is None:
                continue
            for line in eligible_lines:
                item_key = _text(line.get("financial_inventory_item_key"))
                master_item_id = _text(line.get("master_item_id"))
                if not item_key and master_item_id:
                    item_key = financial_inventory_item_key({"master_item_id": master_item_id})
                if not item_key:
                    continue
                if item_key not in item_keys_order:
                    item_keys_order.append(item_key)
                if master_item_id:
                    item_master.setdefault(item_key, master_item_id)
                uom = _text(line.get("base_uom"))
                if uom:
                    item_uoms.setdefault(item_key, set()).add(_normalize_uom(uom))
            continue
        if rid in live_gr_journals or is_cancelled(receipt):
            continue
        ready = readiness_map.get(rid) or {}
        reasons = list(ready.get("fatal_accounting_reasons") or [])
        excluded = list(ready.get("excluded_service_lines") or [])
        if REASON_LEGACY_BEFORE_EFFECTIVE_DATE in reasons or (
            ready.get("in_scope_by_date") is False and settings.get("inventory_configured")
        ):
            pre_effective.append(_ref("goods_receipt", rid, {"receipt_number": receipt.get("receipt_number")}))
            posting_flags.append(FLAG_PRE_EFFECTIVE)
            continue
        if excluded and not (ready.get("eligible_inventory_lines") or []):
            excluded_service.append(_ref("goods_receipt", rid, {"receipt_number": receipt.get("receipt_number")}))
            posting_flags.append(FLAG_EXCLUDED_SERVICE)
            continue
        if ready.get("eligible_inventory_lines") or ready.get("financial_inventory_eligible"):
            pending_gr.append(_ref("goods_receipt", rid, {"receipt_number": receipt.get("receipt_number")}))
            posting_flags.append(FLAG_PENDING_GR)
        elif excluded:
            excluded_service.append(_ref("goods_receipt", rid, {"receipt_number": receipt.get("receipt_number")}))
            posting_flags.append(FLAG_EXCLUDED_SERVICE)

    for row in evaluations or []:
        mid = _text(row.get("movement_id"))
        if not mid:
            continue
        if mid in live_issue_journals:
            continue
        status = _text(row.get("status"))
        code = _text(row.get("blocker_code"))
        extra = {
            "reference_number": row.get("reference_number"),
            "blocker_code": code or None,
        }
        if status == STATUS_PENDING:
            pending_cogs.append(_ref("inventory_issue", mid, extra))
            posting_flags.append(FLAG_PENDING_COGS)
        elif status == STATUS_WAITING:
            waiting_cogs.append(_ref("inventory_issue", mid, extra))
            posting_flags.append(FLAG_WAITING_EARLIER_ISSUE)
        elif status == STATUS_BLOCKED:
            blocked_cogs.append(_ref("inventory_issue", mid, extra))
            posting_flags.append(FLAG_BLOCKED_COGS)
        elif status == STATUS_EXCLUDED:
            if code in {CODE_SERVICE, REASON_NON_INVENTORY_ITEM, "NON_INVENTORY_ITEM"}:
                excluded_service.append(_ref("inventory_issue", mid, extra))
                posting_flags.append(FLAG_EXCLUDED_SERVICE)
            elif code in {CODE_PRE_EFFECTIVE, REASON_LEGACY_BEFORE_EFFECTIVE_DATE, "LEGACY_BEFORE_EFFECTIVE_DATE"}:
                pre_effective.append(_ref("inventory_issue", mid, extra))
                posting_flags.append(FLAG_PRE_EFFECTIVE)

    journals_after = await _load_all_journals(db)
    fingerprint_after = _journal_fingerprint(journals_after)
    retry_required = fingerprint_before != fingerprint_after
    if retry_required:
        coverage_complete = False
    if unstable or retry_required:
        coverage_complete = False

    master_ids = sorted({
        mid for mid in item_master.values() if mid
    } | {
        key[2:] for key in item_keys_order if key.startswith("m:") and key[2:]
    })
    masters = await _find_by_ids(db, "master_items", "id", master_ids)
    stock_states = await _load_stock_states(db, master_ids)
    physical_in_progress = any(_pending_event_is_active(state) for state in stock_states.values())
    for item_key in item_keys_order:
        mid = item_master.get(item_key) or (item_key[2:] if item_key.startswith("m:") else "")
        if mid and mid not in masters:
            anomalies.append(_anomaly(
                code=CODE_MISSING_MASTER,
                message="Master item historis tidak ditemukan; identitas tersimpan tetap dipakai.",
                item_key=item_key,
                source_id=mid or None,
                blocks_reconciled=False,
            ))

    anomalies = _dedupe_anomalies(anomalies)
    incomplete_anomalies, difference_anomalies = _anomaly_effects(anomalies)
    posting_flags = sorted(set(posting_flags))

    inventory_accounts = []
    for aid in sorted(inventory_account_ids, key=lambda x: (
        _text((coa.get(x) or {}).get("code")), x
    )):
        triplet = _reconciliation_triplet(
            inventory_basis.get(aid, _money_zero()),
            inventory_source.get(aid, _money_zero()),
            inventory_gl.get(aid, _money_zero()),
        )
        acc_incomplete, acc_difference = _anomaly_effects(
            [row for row in anomalies if not row.get("account_id") or row.get("account_id") == aid]
        )
        status = _value_status(
            basis=inventory_basis.get(aid, _money_zero()),
            source=inventory_source.get(aid, _money_zero()),
            gl=inventory_gl.get(aid, _money_zero()),
            coverage_complete=coverage_complete,
            incomplete_anomalies=acc_incomplete,
            difference_anomalies=acc_difference,
            unstable=unstable or retry_required,
        )
        row = _account_payload(aid, coa, "inventory")
        row.update(triplet)
        row["financial_inventory_basis_value"] = triplet["financial_basis_value"]
        row["inventory_source_journal_balance"] = triplet["source_journal_balance"]
        row["inventory_gl_balance"] = triplet["gl_balance"]
        row["value_status"] = status
        inventory_accounts.append(row)

    cogs_accounts = []
    for aid in sorted(cogs_account_ids, key=lambda x: (
        _text((coa.get(x) or {}).get("code")), x
    )):
        triplet = _reconciliation_triplet(
            cogs_basis.get(aid, _money_zero()),
            cogs_source.get(aid, _money_zero()),
            cogs_gl.get(aid, _money_zero()),
        )
        acc_incomplete, acc_difference = _anomaly_effects(
            [row for row in anomalies if not row.get("account_id") or row.get("account_id") == aid]
        )
        status = _value_status(
            basis=cogs_basis.get(aid, _money_zero()),
            source=cogs_source.get(aid, _money_zero()),
            gl=cogs_gl.get(aid, _money_zero()),
            coverage_complete=coverage_complete,
            incomplete_anomalies=acc_incomplete,
            difference_anomalies=acc_difference,
            unstable=unstable or retry_required,
        )
        row = _account_payload(aid, coa, "cogs")
        row.update(triplet)
        row["financial_cogs_basis_value"] = triplet["financial_basis_value"]
        row["cogs_source_journal_balance"] = triplet["source_journal_balance"]
        row["cogs_gl_balance"] = triplet["gl_balance"]
        row["value_status"] = status
        cogs_accounts.append(row)

    inv_basis_total = money_q(sum((inventory_basis.get(a, _money_zero()) for a in inventory_account_ids), _money_zero()))
    inv_source_total = money_q(sum((inventory_source.get(a, _money_zero()) for a in inventory_account_ids), _money_zero()))
    inv_gl_total = money_q(sum((inventory_gl.get(a, _money_zero()) for a in inventory_account_ids), _money_zero()))
    cogs_basis_total = money_q(sum((cogs_basis.get(a, _money_zero()) for a in cogs_account_ids), _money_zero()))
    cogs_source_total = money_q(sum((cogs_source.get(a, _money_zero()) for a in cogs_account_ids), _money_zero()))
    cogs_gl_total = money_q(sum((cogs_gl.get(a, _money_zero()) for a in cogs_account_ids), _money_zero()))

    inventory_status = _value_status(
        basis=inv_basis_total,
        source=inv_source_total,
        gl=inv_gl_total,
        coverage_complete=coverage_complete,
        incomplete_anomalies=incomplete_anomalies,
        difference_anomalies=difference_anomalies,
        unstable=unstable or retry_required,
    )
    cogs_status = _value_status(
        basis=cogs_basis_total,
        source=cogs_source_total,
        gl=cogs_gl_total,
        coverage_complete=coverage_complete,
        incomplete_anomalies=incomplete_anomalies,
        difference_anomalies=difference_anomalies,
        unstable=unstable or retry_required,
    )
    # Per-account differences must not cancel into a clean company status.
    account_statuses = [row["value_status"] for row in inventory_accounts + cogs_accounts]
    if any(row["value_status"] == STATUS_DIFFERENCE for row in inventory_accounts + cogs_accounts):
        inventory_status = STATUS_DIFFERENCE if inventory_status != STATUS_INCOMPLETE else inventory_status
        cogs_status = cogs_status if cogs_status == STATUS_INCOMPLETE else (
            STATUS_DIFFERENCE if any(r["value_status"] == STATUS_DIFFERENCE for r in cogs_accounts) else cogs_status
        )
    overall = _combine_status([inventory_status, cogs_status, *account_statuses])
    if retry_required:
        stability = STABILITY_RETRY_REQUIRED
        overall = STATUS_INCOMPLETE
    elif unstable:
        stability = STABILITY_IN_PROGRESS
        overall = STATUS_INCOMPLETE
    else:
        stability = STABILITY_STABLE
    if physical_in_progress:
        if stability == STABILITY_STABLE:
            stability = STABILITY_IN_PROGRESS
        overall = STATUS_INCOMPLETE

    recorded_activity = bool(
        live_gr_journals
        or live_issue_journals
        or family_journals
        or any(not _is_zero_money(v) for v in all_signed_by_account.values())
        or pending_gr or pending_cogs or waiting_cogs or blocked_cogs
        or pre_effective or excluded_service or recovery_refs
        or anomalies
    )

    items = _build_item_rows(
        item_keys=item_keys_order,
        item_master=item_master,
        item_acq_qty=item_acq_qty,
        item_issue_qty=item_issue_qty,
        item_uoms=item_uoms,
        masters=masters,
        stock_states=stock_states,
        acquisitions=acquisitions,
        anomalies=anomalies,
    )
    anomalies = _dedupe_anomalies(anomalies)

    unresolved_rows = []
    for aid in sorted(unresolved_accounts):
        unresolved_rows.append({
            "account_id": aid,
            "signed_balance": money_float(unresolved_accounts[aid]),
            "coa_resolved": False,
        })

    return {
        "generated_at": generated_at,
        "scope": SCOPE_CURRENT,
        "historical_as_of": False,
        "inventory_accounting_effective_date": effective_date,
        "read_stability": stability,
        "basis_coverage_complete": coverage_complete and not unresolved_rows,
        "reconciliation_status": overall,
        "has_activity": recorded_activity,
        "activity_state": ACTIVITY_PRESENT if recorded_activity else ACTIVITY_NONE,
        "fifo_used": fifo_used,
        "rata_rata_used": rata_rata_used,
        "posting_flags": posting_flags,
        "integrity_anomalies": anomalies,
        "unresolved_accounts": unresolved_rows,
        "inventory": {
            **_reconciliation_triplet(inv_basis_total, inv_source_total, inv_gl_total),
            "financial_inventory_basis_value": money_float(inv_basis_total),
            "inventory_source_journal_balance": money_float(inv_source_total),
            "inventory_gl_balance": money_float(inv_gl_total),
            "value_status": inventory_status,
            "accounts": inventory_accounts,
        },
        "cogs": {
            **_reconciliation_triplet(cogs_basis_total, cogs_source_total, cogs_gl_total),
            "financial_cogs_basis_value": money_float(cogs_basis_total),
            "cogs_source_journal_balance": money_float(cogs_source_total),
            "cogs_gl_balance": money_float(cogs_gl_total),
            "value_status": cogs_status,
            "accounts": cogs_accounts,
        },
        "pending": {
            "active_gr_candidates_without_journal": _pending_bucket(pending_gr),
            "pending_cogs": _pending_bucket(pending_cogs),
            "waiting_earlier_issue": _pending_bucket(waiting_cogs),
            "blocked_cogs": _pending_bucket(blocked_cogs),
            "pre_effective_legacy": _pending_bucket(pre_effective),
            "excluded_service": _pending_bucket(excluded_service),
            "accounting_recovery_required": _pending_bucket(recovery_refs),
        },
        "items": items,
        "filters": {
            "summary_scope": "global_current_recorded_state",
            "item_filter_affects": "item_presentation_only",
            "account_filter_affects": "account_row_visibility_only",
        },
    }


def _physical_state(
    state: Optional[dict],
) -> Tuple[Optional[Decimal], Optional[str], bool, bool]:
    pending_active = _pending_event_is_active(state)
    if not state:
        return None, None, False, pending_active
    if not state.get("initialized") or _text(state.get("status")) != STATUS_INITIALIZED:
        return None, _text(state.get("base_uom")) or None, False, pending_active
    try:
        qty = parse_qty(state.get("total_qty") if state.get("total_qty") not in (None, "") else 0, field="total_qty")
    except InvalidQtyError:
        return None, _text(state.get("base_uom")) or None, False, pending_active
    return qty, _text(state.get("base_uom")) or None, True, pending_active


def _build_item_rows(
    *,
    item_keys: Sequence[str],
    item_master: Dict[str, str],
    item_acq_qty: Dict[str, Decimal],
    item_issue_qty: Dict[str, Decimal],
    item_uoms: Dict[str, set],
    masters: Dict[str, dict],
    stock_states: Dict[str, dict],
    acquisitions: Dict[str, dict],
    anomalies: List[dict],
) -> List[dict]:
    rows: List[dict] = []
    seen_physical: set = set()
    for item_key in item_keys:
        mid = item_master.get(item_key) or (item_key[2:] if item_key.startswith("m:") else "")
        master = masters.get(mid) or {}
        financial_qty = item_acq_qty.get(item_key, Decimal("0")) - item_issue_qty.get(item_key, Decimal("0"))
        financial_uoms = {u for u in (item_uoms.get(item_key) or set()) if u}
        physical_qty, physical_uom, physical_ready, pending_active = _physical_state(
            stock_states.get(mid)
        )
        # Only stored physical/financial UOM evidence is comparable. Never
        # substitute the current master item unit for a missing historical UOM.
        physical_uom_n = _normalize_uom(physical_uom) if physical_uom else ""
        financial_uom_n = next(iter(financial_uoms)) if len(financial_uoms) == 1 else ""
        quantity_comparable = False
        qty_difference: Optional[Decimal] = None
        quantity_status = QTY_INCOMPLETE
        base_uom = financial_uom_n or physical_uom_n or None
        if pending_active:
            quantity_status = QTY_IN_PROGRESS
            anomalies.append(_anomaly(
                code=CODE_PHYSICAL_STOCK_IN_PROGRESS,
                message=(
                    "Stok fisik memiliki mutasi yang belum selesai. "
                    "Rekonsiliasi kuantitas harus diulang."
                ),
                item_key=item_key,
                source_id=mid or None,
                blocks_reconciled=False,
            ))
        elif len(financial_uoms) > 1:
            quantity_status = QTY_UNIT_CONFLICT
            anomalies.append(_anomaly(
                code=CODE_UNIT_CONFLICT,
                message="Snapshot finansial item memakai lebih dari satu base UOM.",
                item_key=item_key,
                source_id=mid or None,
                blocks_reconciled=False,
            ))
        elif not physical_ready:
            quantity_status = QTY_MISSING_PHYSICAL
            base_uom = financial_uom_n or None
        elif not financial_uom_n or not physical_uom_n:
            quantity_status = QTY_INCOMPLETE
            quantity_comparable = False
            qty_difference = None
            base_uom = financial_uom_n or physical_uom_n or None
        elif physical_uom_n != financial_uom_n:
            quantity_status = QTY_UOM_MISMATCH
            anomalies.append(_anomaly(
                code=CODE_UOM_MISMATCH,
                message="Base UOM fisik dan finansial tidak kompatibel; tidak dikonversi.",
                item_key=item_key,
                source_id=mid or None,
                blocks_reconciled=False,
            ))
        elif physical_qty is None:
            quantity_status = QTY_MISSING_PHYSICAL
            base_uom = financial_uom_n
        else:
            base_uom = financial_uom_n
            quantity_comparable = True
            qty_difference = physical_qty - financial_qty
            quantity_status = QTY_MATCHED if qty_difference == 0 else QTY_DIFFERENCE

        # Physical qty is attached once per master item, never copied per GL account.
        physical_out = physical_qty
        if mid and mid in seen_physical:
            physical_out = physical_qty
        if mid:
            seen_physical.add(mid)

        accounts = sorted({
            acq["inventory_account_id"]
            for acq in acquisitions.values()
            if acq.get("financial_inventory_item_key") == item_key and acq.get("inventory_account_id")
        })
        rows.append({
            "financial_inventory_item_key": item_key,
            "master_item_id": mid or None,
            "master_item_name": master.get("item_name") or master.get("name"),
            "master_missing": bool(mid) and mid not in masters,
            "base_uom": base_uom,
            "physical_qty": _qty_float(physical_out if physical_ready else None),
            "financial_qty": _qty_float(financial_qty),
            "qty_difference": _qty_float(qty_difference) if quantity_comparable else None,
            "quantity_comparable": quantity_comparable,
            "quantity_status": quantity_status,
            "inventory_account_ids": accounts,
        })
    rows.sort(key=lambda r: (
        _text(r.get("master_item_id")),
        _text(r.get("financial_inventory_item_key")),
    ))
    return rows


def _scope_metadata(ctx: dict) -> dict:
    return {
        "scope": ctx["scope"],
        "historical_as_of": ctx["historical_as_of"],
        "generated_at": ctx["generated_at"],
        "inventory_accounting_effective_date": ctx["inventory_accounting_effective_date"],
        "read_stability": ctx["read_stability"],
        "basis_coverage_complete": ctx["basis_coverage_complete"],
        "reconciliation_status": ctx["reconciliation_status"],
        "has_activity": ctx["has_activity"],
        "activity_state": ctx["activity_state"],
        "filters": ctx["filters"],
    }


def _common_body(ctx: dict) -> dict:
    body = _scope_metadata(ctx)
    body.update({
        "inventory": {
            "financial_inventory_basis_value": ctx["inventory"]["financial_inventory_basis_value"],
            "inventory_source_journal_balance": ctx["inventory"]["inventory_source_journal_balance"],
            "inventory_gl_balance": ctx["inventory"]["inventory_gl_balance"],
            "basis_vs_source_difference": ctx["inventory"]["basis_vs_source_difference"],
            "other_journal_balance": ctx["inventory"]["other_journal_balance"],
            "gl_vs_basis_difference": ctx["inventory"]["gl_vs_basis_difference"],
            "value_status": ctx["inventory"]["value_status"],
            "identity_holds": ctx["inventory"]["identity_holds"],
        },
        "cogs": {
            "financial_cogs_basis_value": ctx["cogs"]["financial_cogs_basis_value"],
            "cogs_source_journal_balance": ctx["cogs"]["cogs_source_journal_balance"],
            "cogs_gl_balance": ctx["cogs"]["cogs_gl_balance"],
            "basis_vs_source_difference": ctx["cogs"]["basis_vs_source_difference"],
            "other_journal_balance": ctx["cogs"]["other_journal_balance"],
            "gl_vs_basis_difference": ctx["cogs"]["gl_vs_basis_difference"],
            "value_status": ctx["cogs"]["value_status"],
            "identity_holds": ctx["cogs"]["identity_holds"],
        },
        "pending": ctx["pending"],
        "posting_flags": ctx["posting_flags"],
        "integrity_anomalies": ctx["integrity_anomalies"],
        "unresolved_accounts": ctx["unresolved_accounts"],
    })
    return body


def _item_matches(row: dict, needle: str) -> bool:
    blob = " ".join([
        _text(row.get("financial_inventory_item_key")),
        _text(row.get("master_item_id")),
        _text(row.get("master_item_name")),
        _text(row.get("base_uom")),
    ]).lower()
    return needle in blob


async def get_inventory_reconciliation_summary(db) -> dict:
    ctx = await build_inventory_reconciliation_context(db)
    body = _common_body(ctx)
    body["account_count"] = {
        "inventory": len(ctx["inventory"]["accounts"]),
        "cogs": len(ctx["cogs"]["accounts"]),
    }
    body["item_count"] = len(ctx["items"])
    return body


async def get_inventory_reconciliation_accounts(
    db,
    *,
    account_id: Optional[str] = None,
) -> dict:
    ctx = await build_inventory_reconciliation_context(db)
    body = _common_body(ctx)
    inventory_rows = list(ctx["inventory"]["accounts"])
    cogs_rows = list(ctx["cogs"]["accounts"])
    wanted = _text(account_id)
    if wanted:
        inventory_rows = [row for row in inventory_rows if row.get("account_id") == wanted]
        cogs_rows = [row for row in cogs_rows if row.get("account_id") == wanted]
    body["inventory_accounts"] = inventory_rows
    body["cogs_accounts"] = cogs_rows
    body["total"] = len(inventory_rows) + len(cogs_rows)
    body["global_account_count"] = {
        "inventory": len(ctx["inventory"]["accounts"]),
        "cogs": len(ctx["cogs"]["accounts"]),
    }
    return body


async def get_inventory_reconciliation_items(
    db,
    *,
    q: Optional[str] = None,
    item: Optional[str] = None,
) -> dict:
    ctx = await build_inventory_reconciliation_context(db)
    body = _common_body(ctx)
    rows = list(ctx["items"])
    item_key = _text(item)
    if item_key:
        rows = [
            row for row in rows
            if row.get("financial_inventory_item_key") == item_key
            or row.get("master_item_id") == item_key
        ]
    needle = _text(q).lower()
    if needle:
        rows = [row for row in rows if _item_matches(row, needle)]
    body["items"] = rows
    body["total"] = len(rows)
    body["global_item_count"] = len(ctx["items"])
    return body
