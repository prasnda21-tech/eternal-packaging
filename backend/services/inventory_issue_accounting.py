"""Financial inventory-issue (COGS) evaluation.

Read-only helpers for PENGIRIMAN eligibility, WIB journal date, live GR
acquisitions, FIFO / RATA-RATA coverage, chronology, and GET queue views.
Does not persist blockers, locks, policy, or journals.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.financial_inventory_opening import wib_business_date
from services.inventory_accounting import (
    ITEM_TYPE_INVENTORY,
    ITEM_TYPE_SERVICE,
    classify_inventory_item_type,
    financial_inventory_item_key,
    inventory_event_in_accounting_scope,
    parse_business_date,
)
from services.inventory_financial_locks import lock_is_active
from services.journal_core import JOURNAL_ENTRIES, STATUS_POSTED
from services.product_unit_conversion import normalize_uom
from services.purchase_accounting_settings import (
    _load_account,
    _validate_cogs,
    _validate_inventory,
    get_purchase_accounting_settings,
)
from services.purchase_common import money_round, now_iso

SOURCE_TYPE = "inventory_issue"
SOURCE_GR = "purchase_goods_receipt"

COLLECTION = "inventory_issue_accounting"
CONTROL_COLLECTION = "inventory_financial_control"
CONTROL_KEY = "cogs_policy"
HPP_SETTINGS_KEY = "hpp_valuation"
STOCK_MOVEMENTS = "stock_movements"
GOODS_RECEIPTS = "purchase_goods_receipts"

MOVEMENT_PENGIRIMAN = "PENGIRIMAN"
METHOD_FIFO = "FIFO"
METHOD_AVG = "RATA-RATA"
METHOD_CHOICES = (METHOD_FIFO, METHOD_AVG)

WIB = timezone(timedelta(hours=7))
MONEY_Q = Decimal("0.01")

STATUS_PENDING = "pending"
STATUS_WAITING = "waiting_earlier_issue"
STATUS_BLOCKED = "blocked"
STATUS_POSTED = "posted"
STATUS_EXCLUDED = "excluded"

CLAIM_FIELD = "accounting_posting_claim"
CLAIM_AT_FIELD = "accounting_posting_claim_at"
POSTING_BASIS_FIELD = "accounting_posting_basis"
ACCOUNTING_CLAIM_TTL_SECONDS = 600

CODE_MISSING_MASTER = "MISSING_MASTER_ITEM"
CODE_UNKNOWN_TYPE = "UNKNOWN_ITEM_TYPE"
CODE_INVALID_UOM = "INVALID_FINANCIAL_UOM"
CODE_INVALID_QTY = "INVALID_ISSUE_QTY"
CODE_INVALID_TS = "INVALID_MOVEMENT_TIMESTAMP"
CODE_INSUFFICIENT = "INSUFFICIENT_FINANCIAL_INVENTORY"
CODE_METHOD_MISMATCH = "FINANCIAL_COGS_METHOD_MISMATCH"
CODE_ACCOUNT_MIGRATION = "INVENTORY_ACCOUNT_MIGRATION_REQUIRED"
CODE_LOCK_CONFLICT = "FINANCIAL_ITEM_LOCK_CONFLICT"
CODE_ALREADY_CONSUMED = "FINANCIAL_INVENTORY_ALREADY_CONSUMED"
CODE_WAITING = "WAITING_EARLIER_ISSUE"
CODE_NOT_PENGIRIMAN = "NOT_PENGIRIMAN"
CODE_VOID = "MOVEMENT_VOID"
CODE_SERVICE = "NON_INVENTORY_ITEM"
CODE_PRE_EFFECTIVE = "LEGACY_BEFORE_EFFECTIVE_DATE"
CODE_COGS_ACCOUNT = "COGS_ACCOUNT_NOT_CONFIGURED"
CODE_HPP_METHOD = "INVALID_HPP_METHOD"
CODE_INVENTORY_ACCOUNT = "INVENTORY_ACCOUNT_INVALID"
CODE_PERIOD_LOCKED = "PERIOD_LOCKED"
CODE_INVALID_ACQUISITION_BASIS = "INVALID_ACQUISITION_BASIS"
CODE_CHRONOLOGY_CONFLICT = "FINANCIAL_CHRONOLOGY_CONFLICT"
MSG_CHRONOLOGY_CONFLICT = (
    "Pengeluaran HPP yang lebih baru sudah diposting. Pengeluaran bertanggal "
    "lebih lama tidak dapat diposting tanpa koreksi histori HPP."
)

CHRONOLOGY_BLOCK_CODES = frozenset({
    CODE_MISSING_MASTER,
    CODE_UNKNOWN_TYPE,
    CODE_INVALID_UOM,
    CODE_INVALID_QTY,
    CODE_INVALID_TS,
    CODE_INSUFFICIENT,
    CODE_METHOD_MISMATCH,
    CODE_ACCOUNT_MIGRATION,
    CODE_LOCK_CONFLICT,
    CODE_COGS_ACCOUNT,
    CODE_HPP_METHOD,
    CODE_INVENTORY_ACCOUNT,
    CODE_PERIOD_LOCKED,
    CODE_CHRONOLOGY_CONFLICT,
})

PERMANENT_EXCLUSION_CODES = frozenset({
    CODE_SERVICE,
    CODE_PRE_EFFECTIVE,
})


class InventoryIssueError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_detail(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value if value is not None else 0))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal("0")


def _money(value: Any) -> Decimal:
    return _dec(value).quantize(MONEY_Q, rounding=ROUND_HALF_UP)


def _qty(value: Any) -> Decimal:
    return _dec(value)


def actor_snapshot(user: Optional[dict]) -> Dict[str, str]:
    user = user or {}
    return {
        "id": str(user.get("id") or user.get("_id") or user.get("user_id") or ""),
        "name": str(user.get("name") or user.get("email") or ""),
        "role": str(user.get("role") or ""),
    }


def parse_financial_hpp_method(raw: Any) -> Optional[str]:
    text = str(raw or "").strip().upper().replace(" ", "-").replace("_", "-")
    if text == METHOD_FIFO:
        return METHOD_FIFO
    if text == METHOD_AVG:
        return METHOD_AVG
    return None


def is_active_movement(movement: Optional[dict]) -> bool:
    row = movement or {}
    status = str(row.get("status") or "").strip().upper()
    if status in {"VOID", "CANCELLED"}:
        return False
    if row.get("voided_at"):
        return False
    return True


def is_pengiriman(movement: Optional[dict]) -> bool:
    return str((movement or {}).get("movement_type") or "").strip().upper() == MOVEMENT_PENGIRIMAN


def _looks_date_only(raw: Any) -> bool:
    if isinstance(raw, datetime):
        return False
    text = str(raw or "").strip()
    if not text:
        return False
    if len(text) == 10 and text[4] == "-" and text[7] == "-":
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return False
        return True
    return False


def parse_movement_instant(raw: Any) -> Optional[datetime]:
    """Parse a full movement datetime. Date-only / missing / unparseable → None.

    Aware timestamps keep their exact instant. Naive full datetimes are UTC.
    Does not mutate the source value.
    """
    if raw is None:
        return None
    if isinstance(raw, datetime):
        instant = raw
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        return instant.astimezone(timezone.utc)
    if _looks_date_only(raw):
        return None
    text = str(raw or "").strip()
    if not text:
        return None
    if "T" not in text and " " not in text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def wib_journal_date_from_instant(instant: datetime) -> str:
    return instant.astimezone(WIB).date().isoformat()


def movement_journal_date(movement: Optional[dict]) -> Optional[str]:
    instant = parse_movement_instant((movement or {}).get("date"))
    if instant is None:
        return None
    return wib_journal_date_from_instant(instant)


def movement_sort_key(movement: Optional[dict]) -> Tuple[int, datetime, datetime, str]:
    """Deterministic chronology. Invalid timestamps are not assigned a journal date.

    Flag 0 = invalid/missing instant (blocks later rows). Flag 1 = parseable instant.
    Valid rows sort by movement.date, then created_at, then id. No date is invented.
    """
    row = movement or {}
    date_instant = parse_movement_instant(row.get("date"))
    created = parse_movement_instant(row.get("created_at"))
    sentinel = datetime.max.replace(tzinfo=timezone.utc)
    invalid = 0 if date_instant is None else 1
    return (
        invalid,
        date_instant or sentinel,
        created or sentinel,
        str(row.get("id") or row.get("_id") or ""),
    )


def movement_id_of(movement: Optional[dict]) -> str:
    row = movement or {}
    return str(row.get("id") or row.get("_id") or "").strip()


def issue_item_key(movement: Optional[dict]) -> str:
    mid = str((movement or {}).get("master_item_id") or "").strip()
    return financial_inventory_item_key({"master_item_id": mid})


def acquisition_key(receipt_id: Any, line_id: Any) -> str:
    return f"{str(receipt_id or '').strip()}:{str(line_id or '').strip()}"


def validate_financial_uom(movement: Optional[dict]) -> Optional[str]:
    row = movement or {}
    tx_qty = _qty(row.get("transaction_qty"))
    base_qty = _qty(row.get("base_qty"))
    tx_uom = normalize_uom(row.get("transaction_uom"))
    base_uom = normalize_uom(row.get("base_uom"))
    factor = _dec(row.get("conversion_factor"))
    if tx_qty <= 0 or base_qty <= 0:
        return CODE_INVALID_QTY
    if not tx_uom or not base_uom:
        return CODE_INVALID_UOM
    if factor <= 0:
        return CODE_INVALID_UOM
    if tx_uom != base_uom and factor == Decimal("1"):
        return CODE_INVALID_UOM
    return None


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index("movement_id", unique=True)
    await db[COLLECTION].create_index("financial_inventory_item_key")
    await db[COLLECTION].create_index("status")
    await db[CONTROL_COLLECTION].create_index("key", unique=True)
    from services.inventory_financial_locks import ensure_indexes as ensure_lock_indexes
    await ensure_lock_indexes(db)


async def read_raw_hpp_method(db) -> Optional[str]:
    doc = await db.app_settings.find_one({"key": HPP_SETTINGS_KEY}, {"_id": 0})
    return parse_financial_hpp_method((doc or {}).get("method"))


async def read_frozen_cogs_policy(db) -> Optional[dict]:
    doc = await db[CONTROL_COLLECTION].find_one({"key": CONTROL_KEY}, {"_id": 0})
    if not doc:
        return None
    method = parse_financial_hpp_method(doc.get("costing_method"))
    if not method:
        return None
    out = dict(doc)
    out["costing_method"] = method
    return out


async def infer_frozen_method_from_posted(db) -> Optional[str]:
    policy = await read_frozen_cogs_policy(db)
    if policy:
        return policy.get("costing_method")
    rows = await db[COLLECTION].find(
        {POSTING_BASIS_FIELD: {"$exists": True}},
        {"_id": 0, POSTING_BASIS_FIELD: 1, "accounting_journal_id": 1, "movement_id": 1},
    ).to_list(None)
    if not rows:
        return None
    live = await load_live_issue_journals_by_movement(db)
    for row in rows:
        mid = str(row.get("movement_id") or "")
        if mid not in live:
            continue
        method = parse_financial_hpp_method((row.get(POSTING_BASIS_FIELD) or {}).get("hpp_method"))
        if method:
            return method
    return None


def resolve_evaluation_method(raw_method: Optional[str], frozen_method: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if frozen_method and raw_method and frozen_method != raw_method:
        return frozen_method, CODE_METHOD_MISMATCH
    if frozen_method:
        return frozen_method, None
    if raw_method:
        return raw_method, None
    return None, CODE_HPP_METHOD


async def load_movement(db, movement_id: str) -> Optional[dict]:
    mid = str(movement_id or "").strip()
    if not mid:
        return None
    doc = await db[STOCK_MOVEMENTS].find_one({"id": mid}, {"_id": 0})
    if doc:
        return doc
    doc = await db[STOCK_MOVEMENTS].find_one({"_id": mid}, {"_id": 0})
    if doc:
        doc.setdefault("id", mid)
        return doc
    return None


async def load_pengiriman_movements(db) -> List[dict]:
    rows = await db[STOCK_MOVEMENTS].find(
        {"movement_type": MOVEMENT_PENGIRIMAN},
        {"_id": 0},
    ).to_list(None)
    out = []
    for row in rows or []:
        if not row.get("id") and row.get("_id"):
            row["id"] = str(row["_id"])
        out.append(row)
    return out


async def find_inventory_issue_source_journal(db, movement_id: str) -> Optional[dict]:
    from services.journal_core import find_primary_journal_for_source
    return await find_primary_journal_for_source(db, SOURCE_TYPE, str(movement_id))


async def load_live_issue_journals_by_movement(db) -> Dict[str, dict]:
    rows = await db[JOURNAL_ENTRIES].find(
        {
            "source_type": SOURCE_TYPE,
            "reversal_of": None,
            "status": STATUS_POSTED,
        },
        {"_id": 0},
    ).to_list(None)
    return {str(row.get("source_id") or ""): row for row in rows or [] if row.get("source_id")}


async def load_live_gr_journals_by_receipt(db) -> Dict[str, dict]:
    rows = await db[JOURNAL_ENTRIES].find(
        {
            "source_type": SOURCE_GR,
            "reversal_of": None,
            "status": STATUS_POSTED,
        },
        {"_id": 0},
    ).to_list(None)
    return {str(row.get("source_id") or ""): row for row in rows or [] if row.get("source_id")}


def _line_acquisition(receipt: dict, basis: dict, line: dict) -> Optional[dict]:
    receipt_id = str(basis.get("receipt_id") or receipt.get("id") or "").strip()
    line_id = str(line.get("line_id") or "").strip()
    item_key = str(line.get("financial_inventory_item_key") or "").strip()
    if not receipt_id or not line_id or not item_key:
        return None
    inv_account = str(basis.get("inventory_account_id") or "").strip()
    if not inv_account:
        return None
    base_qty = _qty(line.get("base_qty"))
    if base_qty <= 0:
        return None
    return {
        "acquisition_key": acquisition_key(receipt_id, line_id),
        "receipt_id": receipt_id,
        "receipt_line_id": line_id,
        "purchase_order_line_id": str(line.get("purchase_order_line_id") or ""),
        "financial_inventory_item_key": item_key,
        "inventory_account_id": inv_account,
        "receipt_date": wib_business_date(receipt.get("receipt_date") or basis.get("receipt_date"))
        or str(receipt.get("receipt_date") or basis.get("receipt_date") or ""),
        "base_qty": base_qty,
        "base_unit_cost": _money(line.get("base_unit_cost")),
        "total_cost": _money(line.get("total_cost") or line.get("line_financial_amount")),
        "base_uom": line.get("base_uom"),
    }


def _receipt_item_keys(receipt: dict) -> set:
    keys = set()
    for line in receipt.get("lines") or []:
        if not isinstance(line, dict):
            continue
        key = financial_inventory_item_key(line)
        if key:
            keys.add(key)
    return keys


async def load_live_acquisition_coverage(db) -> Tuple[List[dict], dict]:
    """Read-only acquisition split.

    A live GR whose eligible lines are not a list of dicts is excluded from the
    cost set entirely. Its receipt id and stored item keys are reported so
    costing is not continued from the remaining acquisitions.
    """
    report = {"unresolved": False, "item_keys": set(), "receipt_ids": []}
    live_journals = await load_live_gr_journals_by_receipt(db)
    out: List[dict] = []
    receipts = []
    if live_journals:
        receipts = await db[GOODS_RECEIPTS].find(
            {"id": {"$in": list(live_journals.keys())}},
            {"_id": 0},
        ).to_list(len(live_journals) + 10)
    for receipt in receipts or []:
        rid = str(receipt.get("id") or "")
        journal = live_journals.get(rid)
        if not journal or str(journal.get("status") or "") != STATUS_POSTED:
            continue
        if journal.get("reversal_of") is not None:
            continue
        basis = receipt.get("accounting_posting_basis") or {}
        if not basis:
            continue
        if not isinstance(basis, dict):
            report["receipt_ids"].append(rid)
            keys = _receipt_item_keys(receipt)
            if keys:
                report["item_keys"].update(keys)
            else:
                report["unresolved"] = True
            continue
        lines = basis.get("eligible_inventory_lines")
        if lines is None:
            continue
        if not isinstance(lines, list) or any(not isinstance(line, dict) for line in lines):
            report["receipt_ids"].append(rid)
            keys = _receipt_item_keys(receipt)
            if keys:
                report["item_keys"].update(keys)
            else:
                report["unresolved"] = True
            continue
        for line in lines:
            acq = _line_acquisition(receipt, basis, line)
            if acq:
                out.append(acq)
    from services.financial_inventory_opening import apply_opening_to_acquisitions, load_posted_openings

    openings = await load_posted_openings(db)
    out = apply_opening_to_acquisitions(out, openings, report)
    return out, report


async def load_live_acquisitions(db) -> List[dict]:
    live_journals = await load_live_gr_journals_by_receipt(db)
    out: List[dict] = []
    receipts = []
    if live_journals:
        receipts = await db[GOODS_RECEIPTS].find(
            {"id": {"$in": list(live_journals.keys())}},
            {"_id": 0},
        ).to_list(len(live_journals) + 10)
    for receipt in receipts or []:
        rid = str(receipt.get("id") or "")
        journal = live_journals.get(rid)
        if not journal or str(journal.get("status") or "") != STATUS_POSTED:
            continue
        if journal.get("reversal_of") is not None:
            continue
        basis = receipt.get("accounting_posting_basis") or {}
        if not basis:
            continue
        for line in basis.get("eligible_inventory_lines") or []:
            acq = _line_acquisition(receipt, basis, line)
            if acq:
                out.append(acq)
    from services.financial_inventory_opening import apply_opening_to_acquisitions, load_posted_openings

    openings = await load_posted_openings(db)
    return apply_opening_to_acquisitions(out, openings)


async def load_live_issue_bases(db) -> List[dict]:
    live = await load_live_issue_journals_by_movement(db)
    if not live:
        return []
    rows = await db[COLLECTION].find(
        {"movement_id": {"$in": list(live.keys())}},
        {"_id": 0},
    ).to_list(len(live) + 10)
    out = []
    for row in rows or []:
        mid = str(row.get("movement_id") or "")
        journal = live.get(mid)
        if not journal:
            continue
        basis = row.get(POSTING_BASIS_FIELD) or {}
        if not basis:
            continue
        out.append({
            "movement_id": mid,
            "journal": journal,
            "basis": basis,
            "financial_inventory_item_key": str(
                basis.get("financial_inventory_item_key") or row.get("financial_inventory_item_key") or ""
            ),
            "journal_date": str(basis.get("journal_date") or journal.get("date") or ""),
        })
    return out


def fifo_consumed_totals(live_issue_bases: Iterable[dict]) -> Dict[str, Dict[str, Decimal]]:
    totals: Dict[str, Dict[str, Decimal]] = {}
    for row in live_issue_bases or []:
        journal = (row or {}).get("journal") or {}
        if str(journal.get("source_type") or "") != SOURCE_TYPE:
            continue
        if str(journal.get("status") or "") != STATUS_POSTED:
            continue
        if journal.get("reversal_of") is not None:
            continue
        basis = (row or {}).get("basis") or {}
        for consumed in basis.get("consumed_acquisitions") or []:
            key = str(consumed.get("acquisition_key") or "").strip()
            qty = _qty(consumed.get("base_qty_consumed"))
            if not key or qty <= 0:
                continue
            bucket = totals.setdefault(key, {"qty": Decimal("0"), "value": Decimal("0")})
            bucket["qty"] += qty
            bucket["value"] += _money(consumed.get("total_cost"))
    return totals


def financial_business_date(raw: Any) -> Optional[str]:
    """Explicit YYYY-MM-DD only. Does not invent a business date."""
    return parse_business_date(raw)


def acquisition_available_for_issue(acq: Optional[dict], issue_journal_date: Optional[str]) -> bool:
    """Same-day GR may fund the issue. A later GR must not. Invalid dates are unavailable."""
    receipt_date = financial_business_date((acq or {}).get("receipt_date"))
    issue_date = financial_business_date(issue_journal_date)
    if receipt_date is None or issue_date is None:
        return False
    return receipt_date <= issue_date


def _aware_instant(raw: Any) -> datetime:
    return parse_movement_instant(raw) or datetime.max.replace(tzinfo=timezone.utc)


def current_issue_order_key(
    issue_journal_date: str,
    issue_instant: datetime,
    issue_id: str,
) -> Tuple[str, int, datetime, str]:
    instant = issue_instant if getattr(issue_instant, "tzinfo", None) else issue_instant.replace(tzinfo=timezone.utc)
    return (
        financial_business_date(issue_journal_date) or "",
        1,
        instant,
        str(issue_id or ""),
    )


def posted_issue_order_key(row: Optional[dict]) -> Optional[Tuple[str, int, datetime, str]]:
    src = row or {}
    basis = src.get("basis") or {}
    journal_date = financial_business_date(basis.get("journal_date") or src.get("journal_date"))
    mid = str(src.get("movement_id") or "")
    if not journal_date or not mid:
        return None
    return (journal_date, 1, _aware_instant(basis.get("movement_timestamp")), mid)


def posted_issue_is_before_current(
    row: Optional[dict],
    issue_journal_date: str,
    issue_instant: datetime,
    issue_id: str,
) -> bool:
    key = posted_issue_order_key(row)
    if key is None:
        return False
    if str((row or {}).get("movement_id") or "") == str(issue_id or ""):
        return False
    return key < current_issue_order_key(issue_journal_date, issue_instant, issue_id)


def posted_issue_is_after_current(
    row: Optional[dict],
    issue_journal_date: str,
    issue_instant: datetime,
    issue_id: str,
) -> bool:
    key = posted_issue_order_key(row)
    if key is None:
        return False
    if str((row or {}).get("movement_id") or "") == str(issue_id or ""):
        return False
    return current_issue_order_key(issue_journal_date, issue_instant, issue_id) < key


def later_live_posted_issue(
    live_issue_bases: Iterable[dict],
    *,
    item_key: str,
    issue_journal_date: str,
    issue_instant: datetime,
    issue_id: str,
) -> Optional[dict]:
    """Return a live posted issue whose financial key is strictly after current."""
    wanted = str(item_key or "")
    if not wanted or not financial_business_date(issue_journal_date):
        return None
    for row in live_issue_bases or []:
        if str(row.get("financial_inventory_item_key") or "") != wanted:
            continue
        journal = row.get("journal") or {}
        if journal.get("reversal_of") is not None:
            continue
        if journal and str(journal.get("status") or "") not in {"", STATUS_POSTED}:
            continue
        if posted_issue_is_after_current(row, issue_journal_date, issue_instant, issue_id):
            return row
    return None


def later_posted_issue_conflict(
    *,
    item_key: str,
    issue_journal_date: Optional[str],
    issue_instant: Optional[datetime],
    issue_id: str,
    live_issue_bases: Iterable[dict],
) -> Optional[str]:
    if issue_instant is None or not issue_journal_date:
        return None
    if later_live_posted_issue(
        live_issue_bases,
        item_key=item_key,
        issue_journal_date=issue_journal_date,
        issue_instant=issue_instant,
        issue_id=issue_id,
    ):
        return CODE_CHRONOLOGY_CONFLICT
    return None


def later_posted_issue_conflict_for_movement(
    movement: Optional[dict],
    live_issue_bases: Iterable[dict],
) -> Optional[str]:
    row = movement or {}
    return later_posted_issue_conflict(
        item_key=issue_item_key(row),
        issue_journal_date=movement_journal_date(row),
        issue_instant=parse_movement_instant(row.get("date")),
        issue_id=movement_id_of(row),
        live_issue_bases=live_issue_bases,
    )


def later_posted_issue_conflict_for_basis(
    basis: Optional[dict],
    live_issue_bases: Iterable[dict],
) -> Optional[str]:
    row = basis or {}
    return later_posted_issue_conflict(
        item_key=str(row.get("financial_inventory_item_key") or ""),
        issue_journal_date=str(row.get("journal_date") or "") or None,
        issue_instant=parse_movement_instant(row.get("movement_timestamp")),
        issue_id=str(row.get("movement_id") or ""),
        live_issue_bases=live_issue_bases,
    )


async def assert_no_later_posted_issue(db, movement: Optional[dict] = None, *, basis: Optional[dict] = None) -> None:
    """Hard-block backdated posting after a later live inventory_issue journal."""
    live = await load_live_issue_bases(db)
    code = None
    if movement is not None:
        code = later_posted_issue_conflict_for_movement(movement, live)
    if code is None and basis is not None:
        code = later_posted_issue_conflict_for_basis(basis, live)
    if code:
        raise InventoryIssueError(code, MSG_CHRONOLOGY_CONFLICT)


def acquisitions_available_for_issue(
    acquisitions: Iterable[dict],
    issue_journal_date: Optional[str],
) -> List[dict]:
    return [acq for acq in (acquisitions or []) if acquisition_available_for_issue(acq, issue_journal_date)]


def fifo_remaining_layers(
    acquisitions: List[dict],
    item_key: str,
    consumed: Dict[str, Dict[str, Decimal]],
    *,
    as_of_journal_date: Optional[str] = None,
) -> List[dict]:
    layers = [
        dict(acq) for acq in acquisitions
        if str(acq.get("financial_inventory_item_key") or "") == item_key
    ]
    if as_of_journal_date is not None:
        layers = [
            acq for acq in layers
            if acquisition_available_for_issue(acq, as_of_journal_date)
        ]
    layers.sort(key=lambda a: (
        str(financial_business_date(a.get("receipt_date")) or ""),
        str(a.get("receipt_id") or ""),
        str(a.get("receipt_line_id") or ""),
    ))
    remaining = []
    for acq in layers:
        key = acq["acquisition_key"]
        used = consumed.get(key) or {"qty": Decimal("0"), "value": Decimal("0")}
        left_qty = acq["base_qty"] - used["qty"]
        left_value = acq["total_cost"] - used["value"]
        if left_qty <= 0:
            continue
        row = dict(acq)
        row["remaining_base_qty"] = left_qty
        row["remaining_value"] = left_value
        remaining.append(row)
    return remaining


def compute_fifo_consumption(
    *,
    issue_base_qty: Decimal,
    layers: List[dict],
) -> Tuple[Optional[List[dict]], Optional[str]]:
    needed = _qty(issue_base_qty)
    if needed <= 0:
        return None, CODE_INVALID_QTY
    available = sum((layer["remaining_base_qty"] for layer in layers), Decimal("0"))
    if available < needed:
        return None, CODE_INSUFFICIENT
    consumed: List[dict] = []
    leftover = needed
    for layer in layers:
        if leftover <= 0:
            break
        take = leftover if leftover <= layer["remaining_base_qty"] else layer["remaining_base_qty"]
        if take <= 0:
            continue
        if take == layer["remaining_base_qty"]:
            total_cost = _money(layer["remaining_value"])
        else:
            total_cost = _money(take * layer["base_unit_cost"])
        consumed.append({
            "acquisition_key": layer["acquisition_key"],
            "receipt_id": layer["receipt_id"],
            "receipt_line_id": layer["receipt_line_id"],
            "purchase_order_line_id": layer.get("purchase_order_line_id") or "",
            "inventory_account_id": layer["inventory_account_id"],
            "base_qty_consumed": float(take),
            "base_unit_cost": money_round(layer["base_unit_cost"]),
            "total_cost": money_round(total_cost),
        })
        leftover -= take
    if leftover > 0:
        return None, CODE_INSUFFICIENT
    return consumed, None


def fifo_inventory_credits(consumed: List[dict]) -> List[Tuple[str, Decimal]]:
    grouped: Dict[str, Decimal] = {}
    order: List[str] = []
    for row in consumed:
        aid = str(row.get("inventory_account_id") or "")
        if aid not in grouped:
            order.append(aid)
            grouped[aid] = Decimal("0")
        grouped[aid] += _money(row.get("total_cost"))
    return [(aid, grouped[aid]) for aid in order]


def _rata_event_sort(event: dict) -> Tuple[str, int, datetime, str]:
    business = str(event.get("business_date") or "")
    kind = 0 if event.get("kind") == "acquisition" else 1
    instant = event.get("instant") or datetime.max.replace(tzinfo=timezone.utc)
    ident = str(event.get("sort_id") or "")
    return (business, kind, instant, ident)


def compute_rata_rata(
    *,
    item_key: str,
    issue_base_qty: Decimal,
    issue_instant: datetime,
    issue_id: str,
    issue_journal_date: str,
    acquisitions: List[dict],
    live_issue_bases: List[dict],
    prior_issues_only: bool = True,
) -> Tuple[Optional[dict], Optional[str]]:
    issue_date = financial_business_date(issue_journal_date)
    if not issue_date:
        return None, CODE_INVALID_TS
    current_key = current_issue_order_key(issue_date, issue_instant, issue_id)
    events: List[dict] = []
    pool_keys: List[str] = []
    for acq in acquisitions:
        if str(acq.get("financial_inventory_item_key") or "") != item_key:
            continue
        receipt_date = financial_business_date(acq.get("receipt_date"))
        if receipt_date is None or receipt_date > issue_date:
            continue
        key = str(acq.get("acquisition_key") or "").strip()
        if key:
            pool_keys.append(key)
        events.append({
            "kind": "acquisition",
            "business_date": receipt_date,
            "instant": datetime.max.replace(tzinfo=timezone.utc),
            "sort_id": f"{acq.get('receipt_id')}:{acq.get('receipt_line_id')}",
            "acq": acq,
        })
    for row in live_issue_bases:
        if str(row.get("financial_inventory_item_key") or "") != item_key:
            continue
        mid = str(row.get("movement_id") or "")
        if mid == str(issue_id):
            continue
        basis = row.get("basis") or {}
        journal_date = financial_business_date(basis.get("journal_date") or row.get("journal_date"))
        if not journal_date:
            continue
        instant = _aware_instant(basis.get("movement_timestamp"))
        event_key = (journal_date, 1, instant, mid)
        if prior_issues_only and event_key >= current_key:
            continue
        events.append({
            "kind": "issue",
            "business_date": journal_date,
            "instant": instant,
            "sort_id": mid,
            "issue": row,
        })
    events.sort(key=_rata_event_sort)

    qty = Decimal("0")
    value = Decimal("0")
    per_account: Dict[str, Dict[str, Decimal]] = {}

    def _add_account(account_id: str, dq: Decimal, dv: Decimal) -> None:
        bucket = per_account.setdefault(account_id, {"qty": Decimal("0"), "value": Decimal("0")})
        bucket["qty"] += dq
        bucket["value"] += dv

    for event in events:
        if event["kind"] == "acquisition":
            acq = event["acq"]
            _add_account(str(acq["inventory_account_id"]), acq["base_qty"], acq["total_cost"])
            qty += acq["base_qty"]
            value += acq["total_cost"]
            continue
        basis = (event.get("issue") or {}).get("basis") or {}
        take_qty = _qty(basis.get("issue_base_qty") or basis.get("base_qty"))
        take_val = _money(basis.get("total_cogs"))
        account_id = str(basis.get("inventory_account_id") or "")
        qty -= take_qty
        value -= take_val
        if account_id:
            _add_account(account_id, -take_qty, -take_val)

    needed = _qty(issue_base_qty)
    if qty < needed:
        return None, CODE_INSUFFICIENT
    live_accounts = [
        aid for aid, bucket in per_account.items()
        if bucket["qty"] > 0 or bucket["value"] > 0
    ]
    if len(live_accounts) > 1:
        return None, CODE_ACCOUNT_MIGRATION
    if not live_accounts:
        return None, CODE_INSUFFICIENT
    inventory_account_id = live_accounts[0]
    if needed == qty:
        total_cogs = _money(value)
        qty_after = Decimal("0")
        value_after = Decimal("0")
        average_cost = _money(value / qty) if qty > 0 else Decimal("0")
    else:
        average_cost = value / qty
        total_cogs = _money(needed * average_cost)
        qty_after = qty - needed
        value_after = _money(value - total_cogs)
        average_cost = _money(average_cost)
    prior_ids = [
        str(event.get("sort_id") or "")
        for event in events
        if event.get("kind") == "issue"
    ]
    return {
        "qty_before": qty,
        "inventory_value_before": _money(value),
        "average_cost": average_cost,
        "total_cogs": total_cogs,
        "qty_after": qty_after,
        "inventory_value_after": value_after,
        "inventory_account_id": inventory_account_id,
        "prior_issue_ids": [pid for pid in prior_ids if pid],
        "pool_acquisition_keys": pool_keys,
        "journal_date": issue_journal_date,
        "issue_instant": issue_instant,
    }, None


async def validate_inventory_account(db, account_id: str) -> Optional[str]:
    account = await _load_account(db, account_id)
    try:
        _validate_inventory(account)
    except Exception:
        return CODE_INVENTORY_ACCOUNT
    return None


async def validate_cogs_account(db, account_id: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    aid = str(account_id or "").strip()
    if not aid:
        return None, CODE_COGS_ACCOUNT
    account = await _load_account(db, aid)
    try:
        _validate_cogs(account)
    except Exception:
        return None, CODE_COGS_ACCOUNT
    return aid, None


def financial_uom_fields(movement: dict) -> Dict[str, Any]:
    return {
        "transaction_qty": movement.get("transaction_qty"),
        "transaction_uom": normalize_uom(movement.get("transaction_uom")),
        "conversion_factor": movement.get("conversion_factor"),
        "base_qty": float(_qty(movement.get("base_qty")).copy_abs()),
        "base_uom": normalize_uom(movement.get("base_uom")),
    }


def _blocker(code: Optional[str], message: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    messages = {
        CODE_MISSING_MASTER: "Master item tidak ditemukan. Chronology HPP diblokir.",
        CODE_UNKNOWN_TYPE: "Tipe item tidak dikenali. Chronology HPP diblokir.",
        CODE_INVALID_UOM: "Snapshot UOM finansial tidak valid.",
        CODE_INVALID_QTY: "Qty finansial pengeluaran tidak valid.",
        CODE_INVALID_TS: "Timestamp mutasi tidak valid untuk tanggal jurnal WIB.",
        CODE_INSUFFICIENT: "Persediaan finansial tidak mencukupi untuk HPP.",
        CODE_METHOD_MISMATCH: "Metode HPP finansial tidak sama dengan kebijakan yang sudah dibekukan.",
        CODE_ACCOUNT_MIGRATION: "Sisa persediaan finansial berada di lebih dari satu akun Persediaan.",
        CODE_LOCK_CONFLICT: "Item persediaan sedang dikunci oleh proses accounting lain.",
        CODE_WAITING: "Menunggu pengeluaran finansial sebelumnya untuk item yang sama.",
        CODE_NOT_PENGIRIMAN: "Hanya mutasi PENGIRIMAN yang menjadi sumber HPP.",
        CODE_VOID: "Mutasi sudah di-void / dibatalkan.",
        CODE_SERVICE: "Item jasa tidak dikapitalisasi ke HPP.",
        CODE_PRE_EFFECTIVE: "Tanggal jurnal sebelum Tanggal Mulai Accounting Persediaan.",
        CODE_COGS_ACCOUNT: "Akun HPP / COGS belum dikonfigurasi.",
        CODE_HPP_METHOD: "Metode HPP finansial tidak valid. Pilih FIFO atau RATA-RATA.",
        CODE_INVALID_ACQUISITION_BASIS: (
            "Basis akuisisi GR tidak valid. HPP item ini tidak dihitung dari sisa akuisisi."
        ),
        CODE_INVENTORY_ACCOUNT: "Akun Persediaan historis tidak valid.",
        CODE_PERIOD_LOCKED: "Periode akuntansi sudah ditutup.",
        CODE_ALREADY_CONSUMED: "Persediaan finansial penerimaan ini sudah dikonsumsi HPP.",
        CODE_CHRONOLOGY_CONFLICT: MSG_CHRONOLOGY_CONFLICT,
    }
    if not code:
        return None, None
    return code, message or messages.get(code, code)


def _empty_view(movement_id: str, **extra: Any) -> Dict[str, Any]:
    view = {
        "movement_id": movement_id,
        "movement_timestamp": None,
        "journal_date": None,
        "reference_type": None,
        "reference_id": None,
        "reference_number": None,
        "order_id": None,
        "line_id": None,
        "item": None,
        "financial_inventory_item_key": "",
        "base_qty": None,
        "base_uom": None,
        "hpp_method": None,
        "computed_cogs": None,
        "status": STATUS_BLOCKED,
        "can_post": False,
        "blocker_code": None,
        "blocker_message": None,
        "accounting_journal_id": None,
    }
    view.update(extra)
    return view


def movement_reference_fields(movement: dict) -> Dict[str, Any]:
    return {
        "reference_type": movement.get("reference_type") or movement.get("source_event_type"),
        "reference_id": movement.get("reference_id") or movement.get("source_event_id"),
        "reference_number": movement.get("reference_number"),
        "order_id": movement.get("order_id"),
        "line_id": movement.get("line_id"),
        "line_idx": movement.get("line_idx"),
        "event_key": movement.get("event_key"),
    }


def classify_movement_row(
    movement: dict,
    *,
    master: Optional[dict],
    settings: dict,
    raw_method: Optional[str],
    frozen_method: Optional[str],
    live_journal: Optional[dict],
) -> Dict[str, Any]:
    mid = movement_id_of(movement)
    refs = movement_reference_fields(movement)
    item_key = issue_item_key(movement)
    timestamp = movement.get("date")
    journal_date = movement_journal_date(movement)
    uom = financial_uom_fields(movement)
    item = {
        "master_item_id": str(movement.get("master_item_id") or ""),
        "item_code": (master or {}).get("item_code") or movement.get("item_code"),
        "item_name": (master or {}).get("item_name") or movement.get("item_name"),
        "item_type": (master or {}).get("item_type"),
    }
    base = _empty_view(
        mid,
        movement_timestamp=timestamp,
        journal_date=journal_date,
        financial_inventory_item_key=item_key,
        item=item,
        base_qty=uom["base_qty"],
        base_uom=uom["base_uom"],
        **refs,
    )
    if live_journal and str(live_journal.get("status") or "") == STATUS_POSTED:
        base["status"] = STATUS_POSTED
        base["can_post"] = False
        base["accounting_journal_id"] = live_journal.get("id")
        return base
    if not is_pengiriman(movement):
        code, msg = _blocker(CODE_NOT_PENGIRIMAN)
        base["status"] = STATUS_EXCLUDED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base
    if not is_active_movement(movement):
        code, msg = _blocker(CODE_VOID)
        base["status"] = STATUS_EXCLUDED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base

    if parse_movement_instant(movement.get("date")) is None:
        code, msg = _blocker(CODE_INVALID_TS)
        base["status"] = STATUS_BLOCKED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base

    master_id = str(movement.get("master_item_id") or "").strip()
    if not master_id or not master:
        code, msg = _blocker(CODE_MISSING_MASTER)
        base["status"] = STATUS_BLOCKED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base

    type_ok, type_reason = classify_inventory_item_type(master.get("item_type"))
    if not type_ok:
        if type_reason and str(master.get("item_type") or "").strip().upper() == ITEM_TYPE_SERVICE:
            code, msg = _blocker(CODE_SERVICE)
            base["status"] = STATUS_EXCLUDED
            base["blocker_code"] = code
            base["blocker_message"] = msg
            return base
        code, msg = _blocker(CODE_UNKNOWN_TYPE)
        base["status"] = STATUS_BLOCKED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base

    uom_code = validate_financial_uom(movement)
    if uom_code:
        code, msg = _blocker(uom_code)
        base["status"] = STATUS_BLOCKED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base

    effective = settings.get("inventory_accounting_effective_date")
    if not inventory_event_in_accounting_scope(journal_date, effective):
        if parse_business_date(effective) and journal_date:
            code, msg = _blocker(CODE_PRE_EFFECTIVE)
            base["status"] = STATUS_EXCLUDED
            base["blocker_code"] = code
            base["blocker_message"] = msg
            return base
        code, msg = _blocker(CODE_COGS_ACCOUNT if not settings.get("cogs_account_id") else CODE_PRE_EFFECTIVE)
        if not parse_business_date(effective):
            code, msg = _blocker(CODE_COGS_ACCOUNT)
        base["status"] = STATUS_BLOCKED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base

    method, method_err = resolve_evaluation_method(raw_method, frozen_method)
    if method_err == CODE_HPP_METHOD:
        code, msg = _blocker(CODE_HPP_METHOD)
        base["status"] = STATUS_BLOCKED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base
    base["hpp_method"] = method
    if method_err == CODE_METHOD_MISMATCH:
        code, msg = _blocker(CODE_METHOD_MISMATCH)
        base["status"] = STATUS_BLOCKED
        base["blocker_code"] = code
        base["blocker_message"] = msg
        return base
    base["status"] = STATUS_PENDING
    base["can_post"] = True
    return base


async def _compute_issue_cost(
    db,
    movement: dict,
    *,
    method: str,
    acquisitions: List[dict],
    live_issue_bases: List[dict],
    cogs_account_id: Optional[str],
) -> Tuple[Optional[dict], Optional[str]]:
    item_key = issue_item_key(movement)
    issue_qty = _qty(movement.get("base_qty")).copy_abs()
    instant = parse_movement_instant(movement.get("date"))
    journal_date = movement_journal_date(movement)
    if instant is None or not journal_date:
        return None, CODE_INVALID_TS
    if method == METHOD_FIFO:
        available = acquisitions_available_for_issue(acquisitions, journal_date)
        prior_issues = [
            row for row in live_issue_bases
            if posted_issue_is_before_current(row, journal_date, instant, movement_id_of(movement))
        ]
        consumed_totals = fifo_consumed_totals(prior_issues)
        layers = fifo_remaining_layers(
            available, item_key, consumed_totals, as_of_journal_date=journal_date
        )
        consumed, err = compute_fifo_consumption(issue_base_qty=issue_qty, layers=layers)
        if err or not consumed:
            return None, err or CODE_INSUFFICIENT
        credits = fifo_inventory_credits(consumed)
        for account_id, _amount in credits:
            inv_err = await validate_inventory_account(db, account_id)
            if inv_err:
                return None, inv_err
        total_cogs = _money(sum((_money(row["total_cost"]) for row in consumed), Decimal("0")))
        if total_cogs != sum((_money(row["total_cost"]) for row in consumed), Decimal("0")):
            return None, CODE_INSUFFICIENT
        return {
            "method": METHOD_FIFO,
            "consumed_acquisitions": consumed,
            "inventory_credits": [
                {"inventory_account_id": aid, "amount": money_round(amount)}
                for aid, amount in credits
            ],
            "total_cogs": money_round(total_cogs),
            "cogs_account_id": cogs_account_id,
        }, None

    computed, err = compute_rata_rata(
        item_key=item_key,
        issue_base_qty=issue_qty,
        issue_instant=instant,
        issue_id=movement_id_of(movement),
        issue_journal_date=journal_date,
        acquisitions=acquisitions,
        live_issue_bases=live_issue_bases,
    )
    if err or not computed:
        return None, err or CODE_INSUFFICIENT
    inv_err = await validate_inventory_account(db, str(computed["inventory_account_id"]))
    if inv_err:
        return None, inv_err
    computed["method"] = METHOD_AVG
    computed["cogs_account_id"] = cogs_account_id
    computed["total_cogs"] = money_round(computed["total_cogs"])
    return computed, None


async def evaluate_inventory_issues(db, *, movement_ids: Optional[List[str]] = None) -> List[dict]:
    settings = await get_purchase_accounting_settings(db)
    raw_method = await read_raw_hpp_method(db)
    frozen_method = await infer_frozen_method_from_posted(db)
    movements = await load_pengiriman_movements(db)
    wanted = {str(mid) for mid in movement_ids} if movement_ids is not None else None
    if wanted is not None:
        for mid in wanted:
            if not any(movement_id_of(m) == mid for m in movements):
                found = await load_movement(db, mid)
                if found:
                    movements.append(found)

    mids = [str(m.get("master_item_id") or "").strip() for m in movements]
    masters: Dict[str, dict] = {}
    if any(mids):
        rows = await db.master_items.find(
            {"id": {"$in": [mid for mid in mids if mid]}},
            {"_id": 0},
        ).to_list(len(mids) + 10)
        for row in rows or []:
            masters[str(row.get("id") or "")] = row

    live_journals = await load_live_issue_journals_by_movement(db)
    acquisitions, acquisition_coverage = await load_live_acquisition_coverage(db)
    live_issue_bases = await load_live_issue_bases(db)
    from services.inventory_financial_locks import COLLECTION as LOCKS
    locks = {
        str(row.get("financial_inventory_item_key") or ""): row
        for row in await db[LOCKS].find({}, {"_id": 0}).to_list(None)
    }
    cogs_id, cogs_err = await validate_cogs_account(db, settings.get("cogs_account_id"))

    evaluated: List[dict] = []
    for movement in movements:
        live = live_journals.get(movement_id_of(movement))
        master = masters.get(str(movement.get("master_item_id") or "").strip())
        row = classify_movement_row(
            movement,
            master=master,
            settings=settings,
            raw_method=raw_method,
            frozen_method=frozen_method,
            live_journal=live,
        )
        row["_sort"] = movement_sort_key(movement)
        row["_movement"] = movement
        if row["status"] == STATUS_POSTED:
            posted_basis = next(
                (b.get("basis") for b in live_issue_bases if b.get("movement_id") == row["movement_id"]),
                None,
            )
            if posted_basis:
                row["hpp_method"] = posted_basis.get("hpp_method")
                row["computed_cogs"] = posted_basis.get("total_cogs")
                row["accounting_journal_id"] = live.get("id") if live else row.get("accounting_journal_id")
            evaluated.append(row)
            continue
        if row["status"] in {STATUS_EXCLUDED, STATUS_BLOCKED}:
            evaluated.append(row)
            continue
        chrono = later_posted_issue_conflict_for_movement(movement, live_issue_bases)
        if chrono:
            code, msg = _blocker(chrono)
            row["status"] = STATUS_BLOCKED
            row["can_post"] = False
            row["blocker_code"] = code
            row["blocker_message"] = msg
            evaluated.append(row)
            continue
        if cogs_err:
            code, msg = _blocker(cogs_err)
            row["status"] = STATUS_BLOCKED
            row["can_post"] = False
            row["blocker_code"] = code
            row["blocker_message"] = msg
            evaluated.append(row)
            continue
        item_key = str(row.get("financial_inventory_item_key") or "")
        invalid_receipts = acquisition_coverage["receipt_ids"]
        affected_by_invalid_basis = (
            acquisition_coverage["unresolved"]
            or (bool(invalid_receipts) and not item_key)
            or item_key in acquisition_coverage["item_keys"]
        )
        if affected_by_invalid_basis:
            code, msg = _blocker(CODE_INVALID_ACQUISITION_BASIS)
            row["status"] = STATUS_BLOCKED
            row["can_post"] = False
            row["computed_cogs"] = None
            row["blocker_code"] = code
            row["blocker_message"] = msg
            evaluated.append(row)
            continue
        method = row.get("hpp_method")
        if method not in METHOD_CHOICES:
            code, msg = _blocker(CODE_HPP_METHOD)
            row["status"] = STATUS_BLOCKED
            row["blocker_code"] = code
            row["blocker_message"] = msg
            evaluated.append(row)
            continue
        computed, err = await _compute_issue_cost(
            db,
            movement,
            method=method,
            acquisitions=acquisitions,
            live_issue_bases=live_issue_bases,
            cogs_account_id=cogs_id,
        )
        if err:
            code, msg = _blocker(err)
            row["status"] = STATUS_BLOCKED
            row["can_post"] = False
            row["blocker_code"] = code
            row["blocker_message"] = msg
            evaluated.append(row)
            continue
        row["computed_cogs"] = (computed or {}).get("total_cogs")
        row["_computed"] = computed
        row["status"] = STATUS_PENDING
        row["can_post"] = True
        evaluated.append(row)

    by_item: Dict[str, List[dict]] = {}
    for row in evaluated:
        by_item.setdefault(str(row.get("financial_inventory_item_key") or ""), []).append(row)
    for item_key, rows in by_item.items():
        rows.sort(key=lambda r: r.get("_sort") or movement_sort_key(r.get("_movement")))
        seen_unresolved = False
        for row in rows:
            if row.get("status") == STATUS_POSTED:
                continue
            if row.get("status") == STATUS_EXCLUDED:
                continue
            if seen_unresolved:
                code, msg = _blocker(CODE_WAITING)
                row["status"] = STATUS_WAITING
                row["can_post"] = False
                row["blocker_code"] = code
                row["blocker_message"] = msg
                row["computed_cogs"] = None
                continue
            seen_unresolved = True
            lock = locks.get(item_key) if item_key else None
            if lock_is_active(lock):
                holder = str((lock or {}).get("holder_movement_id") or "")
                if holder and holder != str(row.get("movement_id") or ""):
                    code, msg = _blocker(CODE_LOCK_CONFLICT)
                    row["status"] = STATUS_BLOCKED
                    row["can_post"] = False
                    row["blocker_code"] = code
                    row["blocker_message"] = msg
                    row["computed_cogs"] = None

    public = []
    for row in evaluated:
        if wanted is not None and str(row.get("movement_id") or "") not in wanted:
            continue
        row.pop("_sort", None)
        row.pop("_movement", None)
        row.pop("_computed", None)
        public.append(row)
    public.sort(key=lambda r: (str(r.get("journal_date") or ""), str(r.get("movement_id") or "")))
    return public


async def evaluate_inventory_issue(db, movement_id: str) -> Optional[dict]:
    rows = await evaluate_inventory_issues(db, movement_ids=[str(movement_id)])
    for row in rows:
        if str(row.get("movement_id") or "") == str(movement_id):
            return row
    movement = await load_movement(db, movement_id)
    if not movement:
        return None
    rows = await evaluate_inventory_issues(db, movement_ids=[str(movement_id)])
    return rows[0] if rows else _empty_view(str(movement_id), status=STATUS_EXCLUDED, **{"blocker_code": CODE_NOT_PENGIRIMAN, "blocker_message": _blocker(CODE_NOT_PENGIRIMAN)[1]})


def public_issue_view(row: Optional[dict]) -> Dict[str, Any]:
    src = row or {}
    return {
        "movement_id": src.get("movement_id"),
        "movement_timestamp": src.get("movement_timestamp"),
        "journal_date": src.get("journal_date"),
        "reference_type": src.get("reference_type"),
        "reference_id": src.get("reference_id"),
        "reference_number": src.get("reference_number"),
        "order_id": src.get("order_id"),
        "line_id": src.get("line_id"),
        "item": src.get("item"),
        "financial_inventory_item_key": src.get("financial_inventory_item_key"),
        "base_qty": src.get("base_qty"),
        "base_uom": src.get("base_uom"),
        "hpp_method": src.get("hpp_method"),
        "computed_cogs": src.get("computed_cogs"),
        "status": src.get("status"),
        "can_post": bool(src.get("can_post")),
        "blocker_code": src.get("blocker_code"),
        "blocker_message": src.get("blocker_message"),
        "accounting_journal_id": src.get("accounting_journal_id"),
    }


def build_cogs_posting_basis(
    *,
    movement: dict,
    evaluation: dict,
    computed: dict,
    settings: dict,
    user: Optional[dict],
    claim_token: str,
) -> Dict[str, Any]:
    refs = movement_reference_fields(movement)
    instant = parse_movement_instant(movement.get("date"))
    journal_date = evaluation.get("journal_date") or movement_journal_date(movement)
    method = computed.get("method")
    uom = financial_uom_fields(movement)
    created = now_iso()
    actor = actor_snapshot(user)
    common = {
        "movement_id": movement_id_of(movement),
        "event_key": refs.get("event_key"),
        "financial_inventory_item_key": evaluation.get("financial_inventory_item_key") or issue_item_key(movement),
        "reference_type": refs.get("reference_type"),
        "reference_id": refs.get("reference_id"),
        "reference_number": refs.get("reference_number"),
        "order_id": refs.get("order_id"),
        "line_id": refs.get("line_id"),
        "line_idx": refs.get("line_idx"),
        "movement_timestamp": movement.get("date") if instant else movement.get("date"),
        "journal_date": journal_date,
        "transaction_qty": uom["transaction_qty"],
        "transaction_uom": uom["transaction_uom"],
        "conversion_factor": uom["conversion_factor"],
        "base_uom": uom["base_uom"],
        "cogs_account_id": computed.get("cogs_account_id"),
        "total_cogs": computed.get("total_cogs"),
        "inventory_accounting_effective_date": settings.get("inventory_accounting_effective_date"),
        "posting_claim_token": str(claim_token),
        "created_at": created,
        "created_by": actor,
        "hpp_method": method,
    }
    if method == METHOD_FIFO:
        consumed = list(computed.get("consumed_acquisitions") or [])
        total = _money(sum((_money(row.get("total_cost")) for row in consumed), Decimal("0")))
        common.update({
            "base_qty": uom["base_qty"],
            "consumed_acquisitions": consumed,
            "total_cogs": money_round(total),
        })
        return common
    common.update({
        "issue_base_qty": uom["base_qty"],
        "qty_before": float(computed.get("qty_before") or 0),
        "inventory_value_before": money_round(computed.get("inventory_value_before") or 0),
        "average_cost": money_round(computed.get("average_cost") or 0),
        "qty_after": float(computed.get("qty_after") or 0),
        "inventory_value_after": money_round(computed.get("inventory_value_after") or 0),
        "inventory_account_id": computed.get("inventory_account_id"),
        "prior_issue_ids": list(computed.get("prior_issue_ids") or []),
        "pool_acquisition_keys": [
            str(key) for key in (computed.get("pool_acquisition_keys") or []) if str(key or "").strip()
        ],
        "total_cogs": money_round(computed.get("total_cogs") or 0),
    })
    return common


def journal_lines_from_basis(basis: dict) -> List[dict]:
    method = parse_financial_hpp_method(basis.get("hpp_method"))
    cogs_id = str(basis.get("cogs_account_id") or "").strip()
    total = _money(basis.get("total_cogs"))
    ref = str(basis.get("reference_number") or basis.get("movement_id") or "pengeluaran")
    lines = [{
        "account_id": cogs_id,
        "debit": total,
        "credit": Decimal("0.00"),
        "description": f"HPP {ref}",
    }]
    if method == METHOD_FIFO:
        credits = fifo_inventory_credits(list(basis.get("consumed_acquisitions") or []))
        for account_id, amount in credits:
            lines.append({
                "account_id": account_id,
                "debit": Decimal("0.00"),
                "credit": amount,
                "description": f"Persediaan {ref}",
            })
    else:
        lines.append({
            "account_id": str(basis.get("inventory_account_id") or ""),
            "debit": Decimal("0.00"),
            "credit": total,
            "description": f"Persediaan {ref}",
        })
    return lines


def gr_item_keys_from_basis(basis: Optional[dict]) -> List[str]:
    keys = []
    for line in (basis or {}).get("eligible_inventory_lines") or []:
        key = str(line.get("financial_inventory_item_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


def gr_acquisition_keys_from_basis(basis: Optional[dict]) -> List[str]:
    receipt_id = str((basis or {}).get("receipt_id") or "").strip()
    keys = []
    for line in (basis or {}).get("eligible_inventory_lines") or []:
        line_id = str(line.get("line_id") or "").strip()
        if receipt_id and line_id:
            keys.append(acquisition_key(receipt_id, line_id))
    return keys


async def unjournaled_basis_still_coverable(db, basis: Optional[dict]) -> Optional[str]:
    """True coverage for a persisted basis that is not yet a live journal.

    Live posted journals are the only consumption authority. If another issue
    already posted against the same acquisitions/qty, this basis must not journal.
    """
    row = basis or {}
    item_key = str(row.get("financial_inventory_item_key") or "").strip()
    method = parse_financial_hpp_method(row.get("hpp_method"))
    mid = str(row.get("movement_id") or "").strip()
    if not item_key or method not in METHOD_CHOICES:
        return CODE_INSUFFICIENT
    acquisitions = await load_live_acquisitions(db)
    live_issues = [
        issue for issue in await load_live_issue_bases(db)
        if str(issue.get("movement_id") or "") != mid
    ]
    chrono = later_posted_issue_conflict_for_basis(row, live_issues)
    if chrono:
        return chrono
    if method == METHOD_FIFO:
        journal_date = str(row.get("journal_date") or "")
        remaining = {
            layer["acquisition_key"]: layer["remaining_base_qty"]
            for layer in fifo_remaining_layers(
                acquisitions_available_for_issue(acquisitions, journal_date),
                item_key,
                fifo_consumed_totals(live_issues),
                as_of_journal_date=journal_date,
            )
        }
        needed_rows = list(row.get("consumed_acquisitions") or [])
        if not needed_rows:
            return CODE_INSUFFICIENT
        for consumed in needed_rows:
            key = str(consumed.get("acquisition_key") or "")
            take = _qty(consumed.get("base_qty_consumed"))
            if take <= 0 or remaining.get(key, Decimal("0")) < take:
                return CODE_INSUFFICIENT
        return None
    issue_qty = _qty(row.get("issue_base_qty") or row.get("base_qty"))
    instant = parse_movement_instant(row.get("movement_timestamp"))
    journal_date = str(row.get("journal_date") or "")
    if instant is None or not journal_date:
        return CODE_INVALID_TS
    _computed, err = compute_rata_rata(
        item_key=item_key,
        issue_base_qty=issue_qty,
        issue_instant=instant,
        issue_id=mid,
        issue_journal_date=journal_date,
        acquisitions=acquisitions,
        live_issue_bases=live_issues,
        prior_issues_only=False,
    )
    return err


async def assert_gr_unconsumed_for_reversal(db, receipt: dict) -> None:
    """Block a fresh GR financial reversal when live COGS already consumed it."""
    basis = (receipt or {}).get("accounting_posting_basis") or {}
    method = await infer_frozen_method_from_posted(db)
    if not method:
        return
    live_issues = await load_live_issue_bases(db)
    if method == METHOD_FIFO:
        keys = set(gr_acquisition_keys_from_basis(basis))
        for row in live_issues:
            for consumed in (row.get("basis") or {}).get("consumed_acquisitions") or []:
                acq_key = str(consumed.get("acquisition_key") or "")
                qty = _qty(consumed.get("base_qty_consumed"))
                if acq_key in keys and qty > 0:
                    code, msg = _blocker(CODE_ALREADY_CONSUMED)
                    raise InventoryIssueError(code, msg)
        return
    receipt_date = str(basis.get("receipt_date") or (receipt or {}).get("receipt_date") or "")[:10]
    item_keys = set(gr_item_keys_from_basis(basis))
    for row in live_issues:
        item_key = str(row.get("financial_inventory_item_key") or "")
        if item_key not in item_keys:
            continue
        journal_date = str((row.get("basis") or {}).get("journal_date") or row.get("journal_date") or "")
        if receipt_date and journal_date and journal_date >= receipt_date:
            code, msg = _blocker(CODE_ALREADY_CONSUMED)
            raise InventoryIssueError(code, msg)
