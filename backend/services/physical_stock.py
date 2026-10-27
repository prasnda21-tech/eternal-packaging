"""Append-only physical stock ledger and balance.

Ledger insert and balance mutation commit in one MongoDB transaction.
There is no non-transactional fallback.

Book physical_qty may go negative when a legitimate outbound posts
(shortage is recorded/warned, not blocked). Target physical counts from
opname/adjustment remain >= 0. Missing opening baseline is tracked as
unreconciled — not treated as a reconciled zero opening.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from services.org_locations import warehouse_is_operational

LEDGER_COLLECTION = "physical_stock_ledger"
BALANCE_COLLECTION = "physical_stock_balances"
SALES_DELIVERY_CONFIRMATIONS = "physical_sales_delivery_confirmations"
MASTER_ITEMS_COLLECTION = "master_items"
BRANCHES_COLLECTION = "branches"
WAREHOUSES_COLLECTION = "warehouses"

BASELINE_RECONCILED = "reconciled"
BASELINE_UNRECONCILED = "unreconciled"
BASELINE_UNRECONCILED_LABEL = "Saldo awal belum direkonsiliasi"
# Honest fallback when a posted movement has no resolvable user identity.
LAST_STOCK_ACTOR_UNKNOWN = "Tidak tercatat"

MOVEMENT_OPENING_BALANCE = "OPENING_BALANCE"
MOVEMENT_PURCHASE_RECEIPT = "PURCHASE_RECEIPT"
MOVEMENT_PURCHASE_RETURN = "PURCHASE_RETURN"
MOVEMENT_SALES_DELIVERY = "SALES_DELIVERY"
MOVEMENT_STOCK_OPNAME_ADJUSTMENT = "STOCK_OPNAME_ADJUSTMENT"
MOVEMENT_MANUAL_ADJUSTMENT = "MANUAL_ADJUSTMENT"
MOVEMENT_WAREHOUSE_TRANSFER_OUT = "WAREHOUSE_TRANSFER_OUT"
MOVEMENT_WAREHOUSE_TRANSFER_IN = "WAREHOUSE_TRANSFER_IN"
MOVEMENT_REVERSAL = "REVERSAL"
MOVEMENT_PRODUCTION_CONSUMPTION = "PRODUCTION_CONSUMPTION"
MOVEMENT_PRODUCTION_OUTPUT = "PRODUCTION_OUTPUT"

MOVEMENT_TYPES = frozenset({
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_PURCHASE_RETURN,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_WAREHOUSE_TRANSFER_OUT,
    MOVEMENT_WAREHOUSE_TRANSFER_IN,
    MOVEMENT_REVERSAL,
    MOVEMENT_PRODUCTION_CONSUMPTION,
    MOVEMENT_PRODUCTION_OUTPUT,
})

# 1 = qty_delta must be positive, -1 = must be negative, 0 = either non-zero.
_MOVEMENT_SIGN = {
    MOVEMENT_OPENING_BALANCE: 1,
    MOVEMENT_PURCHASE_RECEIPT: 1,
    MOVEMENT_PURCHASE_RETURN: -1,
    MOVEMENT_SALES_DELIVERY: -1,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT: 0,
    MOVEMENT_MANUAL_ADJUSTMENT: 0,
    MOVEMENT_WAREHOUSE_TRANSFER_OUT: -1,
    MOVEMENT_WAREHOUSE_TRANSFER_IN: 1,
    MOVEMENT_REVERSAL: 0,
    MOVEMENT_PRODUCTION_CONSUMPTION: -1,
    MOVEMENT_PRODUCTION_OUTPUT: 1,
}

_PAYLOAD_FIELDS = (
    "master_item_id",
    "branch_id",
    "warehouse_id",
    "movement_type",
    "qty_delta",
    "reference_type",
    "reference_id",
    "reason",
)

_MAX_ATTEMPTS = 5

# Tests set this to force an abort after the balance write and before the ledger insert.
_fail_after_balance = False


class PhysicalStockError(Exception):
    """Base error for the physical stock engine."""


class PhysicalStockUnavailableError(PhysicalStockError):
    """Replica-set transactions are not available."""


class PhysicalStockValidationError(PhysicalStockError):
    """Movement payload or master data is not acceptable."""


class PhysicalStockNotFoundError(PhysicalStockError):
    """A requested master item, branch, or warehouse does not exist."""


class PhysicalStockConflictError(PhysicalStockError):
    """Idempotency payload clash or a movement already reversed."""


class InsufficientPhysicalStockError(PhysicalStockError):
    """Legacy/guard error: kept for callers that still reject negatives.

    Legitimate R2 sales delivery / outbound book updates no longer raise this
    solely because the resulting book qty would be negative.
    """


class PhysicalStockAbortError(PhysicalStockError):
    """Injected failure used to prove transaction rollback."""


class PhysicalStockRetryError(PhysicalStockError):
    """The balance changed before the target delta was applied."""


class StockOpnameInProgressError(PhysicalStockConflictError):
    """A frozen stock-opname session owns this warehouse."""


OPNAME_SESSIONS = "stock_opname_sessions"
FROZEN_OPNAME_STATUSES = ("COUNTING", "REVIEW")
STOCK_OPNAME_IN_PROGRESS = "STOCK_OPNAME_IN_PROGRESS"


def set_fail_after_balance(enabled: bool) -> None:
    global _fail_after_balance
    _fail_after_balance = bool(enabled)


_fail_sales_delivery_persist = False


def set_fail_sales_delivery_persist(enabled: bool) -> None:
    global _fail_sales_delivery_persist
    _fail_sales_delivery_persist = bool(enabled)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client(db):
    client = getattr(db, "client", None)
    if client is None:
        raise PhysicalStockUnavailableError("MongoDB client is not available.")
    return client


async def require_transaction_support(client) -> None:
    """Fail fast unless this Mongo deployment can run multi-document transactions."""
    try:
        hello = await client.admin.command("hello")
    except PyMongoError as exc:
        raise PhysicalStockUnavailableError("MongoDB transaction preflight failed.") from exc
    if not hello.get("setName"):
        raise PhysicalStockUnavailableError(
            "MongoDB transactions are unavailable. A replica set is required."
        )
    if not hello.get("isWritablePrimary"):
        raise PhysicalStockUnavailableError(
            "MongoDB transactions require a writable primary."
        )


async def ensure_indexes(db) -> None:
    ledger = db[LEDGER_COLLECTION]
    await ledger.create_index("id", unique=True)
    await ledger.create_index("idempotency_key", unique=True)
    await ledger.create_index(
        [("master_item_id", 1), ("branch_id", 1), ("warehouse_id", 1), ("occurred_at", 1)]
    )
    await ledger.create_index(
        [("movement_type", 1), ("reference_id", 1)],
        unique=True,
        name="reversal_original_unique",
        partialFilterExpression={"movement_type": MOVEMENT_REVERSAL},
    )
    await ledger.create_index(
        [("master_item_id", 1), ("branch_id", 1), ("warehouse_id", 1)],
        unique=True,
        name="opening_balance_identity_unique",
        partialFilterExpression={"movement_type": MOVEMENT_OPENING_BALANCE},
    )
    balances = db[BALANCE_COLLECTION]
    await balances.create_index("id", unique=True)
    await balances.create_index(
        [("master_item_id", 1), ("branch_id", 1), ("warehouse_id", 1)],
        unique=True,
        name="physical_balance_identity",
    )
    await db["physical_opening_imports"].create_index("id", unique=True)
    await db["physical_opening_imports"].create_index("idempotency_key", unique=True)
    confirmations = db[SALES_DELIVERY_CONFIRMATIONS]
    await confirmations.create_index("id", unique=True)


def _as_text(value: Any, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise PhysicalStockValidationError(f"{label} wajib diisi")
    return text


def _as_qty(value: Any, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or (value == 0 and not allow_zero):
        raise PhysicalStockValidationError("qty_delta harus bilangan bulat selain nol")
    return value


def _check_sign(movement_type: str, qty_delta: int) -> None:
    expected = _MOVEMENT_SIGN.get(movement_type)
    if expected is None:
        raise PhysicalStockValidationError("Jenis movement tidak dikenal")
    if expected == 1 and qty_delta < 0:
        raise PhysicalStockValidationError(f"{movement_type} harus menambah stok")
    if expected == -1 and qty_delta > 0:
        raise PhysicalStockValidationError(f"{movement_type} harus mengurangi stok")


def _same_payload(existing: dict, requested: dict) -> bool:
    for field in _PAYLOAD_FIELDS:
        if existing.get(field) != requested.get(field):
            return False
    return True


def _public_movement(doc: dict) -> dict:
    metadata = doc.get("metadata")
    out = {
        "id": doc.get("id"),
        "master_item_id": doc.get("master_item_id"),
        "branch_id": doc.get("branch_id"),
        "warehouse_id": doc.get("warehouse_id"),
        "movement_type": doc.get("movement_type"),
        "qty_delta": doc.get("qty_delta"),
        "balance_before": doc.get("balance_before"),
        "balance_after": doc.get("balance_after"),
        "reference_type": doc.get("reference_type") or "",
        "reference_id": doc.get("reference_id") or "",
        "reason": doc.get("reason") or "",
        "actor_id": doc.get("actor_id") or "",
        "occurred_at": doc.get("occurred_at"),
        "created_at": doc.get("created_at"),
        "idempotency_key": doc.get("idempotency_key"),
    }
    if isinstance(metadata, dict) and metadata:
        out["metadata"] = metadata
    return out


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, DuplicateKeyError):
        return True
    if isinstance(exc, PyMongoError) and exc.has_error_label("TransientTransactionError"):
        return True
    if isinstance(exc, PyMongoError) and exc.has_error_label("UnknownTransactionCommitResult"):
        return True
    code = getattr(exc, "code", None)
    return code in (112, 251)  # WriteConflict, NoSuchTransaction


async def _validate_masters(db, *, master_item_id: str, branch_id: str, warehouse_id: str, session) -> None:
    item = await db[MASTER_ITEMS_COLLECTION].find_one({"id": master_item_id}, {"_id": 1}, session=session)
    if not item:
        raise PhysicalStockValidationError("Barang tidak ditemukan")
    branch = await db[BRANCHES_COLLECTION].find_one(
        {"id": branch_id},
        {"_id": 0, "id": 1, "active": 1},
        session=session,
    )
    if not branch:
        raise PhysicalStockValidationError("Cabang tidak ditemukan")
    if branch.get("active") is not True:
        raise PhysicalStockValidationError("Cabang tidak aktif")
    warehouse = await db[WAREHOUSES_COLLECTION].find_one(
        {"id": warehouse_id},
        {"_id": 0, "id": 1, "branch_id": 1, "active": 1},
        session=session,
    )
    if not warehouse:
        raise PhysicalStockValidationError("Gudang tidak ditemukan")
    if str(warehouse.get("branch_id") or "") != branch_id:
        raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")
    if warehouse.get("active") is not True:
        raise PhysicalStockValidationError("Gudang tidak aktif")


async def _existing_movement(db, idempotency_key: str, session):
    return await db[LEDGER_COLLECTION].find_one(
        {"idempotency_key": idempotency_key},
        {"_id": 0},
        session=session,
    )


def _opname_session_mode(doc: Optional[dict]) -> str:
    """freeze (legacy default) or operational (warehouse stays open)."""
    mode = str((doc or {}).get("mode") or "").strip().lower()
    if mode == "operational":
        return "operational"
    return "freeze"


async def _frozen_opname_session(db, warehouse_id: str, session):
    """Active freeze-mode opname only. Operational sessions do not freeze the warehouse."""
    return await db[OPNAME_SESSIONS].find_one(
        {
            "warehouse_id": str(warehouse_id),
            "status": {"$in": list(FROZEN_OPNAME_STATUSES)},
            "$or": [
                {"mode": {"$exists": False}},
                {"mode": None},
                {"mode": ""},
                {"mode": "freeze"},
            ],
        },
        {"_id": 0, "id": 1, "mode": 1},
        session=session,
    )


async def _guard_opname_freeze(
    db,
    session,
    *,
    warehouse_id: str,
    movement_type: str,
    allow_opname_session_id: Optional[str] = None,
) -> None:
    """Reject normal R2 writes while a freeze-mode stock opname owns the warehouse.

    STOCK_OPNAME_ADJUSTMENT posts only for the owning active session (freeze or
    operational). Operational mode does not block receipt/delivery; freeze mode does.
    Reversal is not exempt from freeze.
    """
    frozen = await _frozen_opname_session(db, warehouse_id, session)
    allowed = str(allow_opname_session_id or "").strip()
    if movement_type == MOVEMENT_STOCK_OPNAME_ADJUSTMENT:
        if not allowed:
            if frozen:
                raise StockOpnameInProgressError(STOCK_OPNAME_IN_PROGRESS)
            raise PhysicalStockValidationError(
                "Penyesuaian stock opname hanya dapat diposting dari konfirmasi sesi yang sedang berjalan"
            )
        owning = await db[OPNAME_SESSIONS].find_one(
            {
                "id": allowed,
                "warehouse_id": str(warehouse_id),
                "status": {"$in": list(FROZEN_OPNAME_STATUSES)},
            },
            {"_id": 0, "id": 1, "mode": 1},
            session=session,
        )
        if not owning:
            if frozen:
                raise StockOpnameInProgressError(STOCK_OPNAME_IN_PROGRESS)
            raise PhysicalStockValidationError(
                "Penyesuaian stock opname hanya dapat diposting dari konfirmasi sesi yang sedang berjalan"
            )
        if _opname_session_mode(owning) == "freeze":
            if frozen and allowed == str(frozen.get("id") or ""):
                return
            if frozen:
                raise StockOpnameInProgressError(STOCK_OPNAME_IN_PROGRESS)
            raise PhysicalStockValidationError(
                "Penyesuaian stock opname hanya dapat diposting dari konfirmasi sesi yang sedang berjalan"
            )
        # operational: warehouse not frozen by this session; still block if another freeze owns it
        if frozen and allowed != str(frozen.get("id") or ""):
            raise StockOpnameInProgressError(STOCK_OPNAME_IN_PROGRESS)
        return
    if frozen:
        raise StockOpnameInProgressError(STOCK_OPNAME_IN_PROGRESS)


async def _apply_movement_in_session(
    db,
    requested: dict,
    session,
    *,
    allow_opname_session_id: Optional[str] = None,
) -> dict:
    """Post one movement inside the caller's transaction. No nested transaction."""
    ledger = db[LEDGER_COLLECTION]
    balances = db[BALANCE_COLLECTION]
    existing = await _existing_movement(db, requested["idempotency_key"], session)
    if existing:
        if not _same_payload(existing, requested):
            raise PhysicalStockConflictError(
                "Idempotency key sudah digunakan dengan payload yang berbeda"
            )
        return _public_movement(existing)

    await _guard_opname_freeze(
        db,
        session,
        warehouse_id=requested["warehouse_id"],
        movement_type=requested["movement_type"],
        allow_opname_session_id=allow_opname_session_id,
    )
    await _validate_masters(
        db,
        master_item_id=requested["master_item_id"],
        branch_id=requested["branch_id"],
        warehouse_id=requested["warehouse_id"],
        session=session,
    )
    if requested["movement_type"] == MOVEMENT_REVERSAL:
        if not requested["reference_id"]:
            raise PhysicalStockValidationError("Reversal wajib merujuk movement asal")
        prior = await ledger.find_one(
            {
                "movement_type": MOVEMENT_REVERSAL,
                "reference_id": requested["reference_id"],
            },
            {"_id": 1},
            session=session,
        )
        if prior:
            raise PhysicalStockConflictError("Movement asal sudah direversal")

    movement_id = str(uuid.uuid4())
    identity = {
        "master_item_id": requested["master_item_id"],
        "branch_id": requested["branch_id"],
        "warehouse_id": requested["warehouse_id"],
    }
    qty_delta = requested["qty_delta"]
    created_at = requested["created_at"]
    if requested["movement_type"] == MOVEMENT_OPENING_BALANCE:
        prior_ledger = await ledger.find_one(identity, {"_id": 1}, session=session)
        if prior_ledger:
            raise PhysicalStockConflictError(
                "Saldo awal ditolak karena identitas ini sudah memiliki mutasi stok fisik"
            )
        prior_balance = await balances.find_one(identity, {"_id": 1}, session=session)
        if prior_balance:
            raise PhysicalStockConflictError(
                "Saldo awal ditolak karena saldo fisik sudah ada"
            )
    movement_type = requested["movement_type"]
    establishes_reconciled_baseline = movement_type in (
        MOVEMENT_OPENING_BALANCE,
        MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        MOVEMENT_MANUAL_ADJUSTMENT,
    )
    # Mongo forbids the same path in both $set and $setOnInsert.
    set_fields = {"last_movement_id": movement_id, "updated_at": created_at}
    set_on_insert = {
        "id": str(uuid.uuid4()),
        "created_at": created_at,
    }
    if establishes_reconciled_baseline:
        set_fields["baseline_status"] = BASELINE_RECONCILED
    else:
        set_on_insert["baseline_status"] = BASELINE_UNRECONCILED

    # Outbound may create the first balance row (unreconciled baseline) and may
    # drive book qty negative. Do not invent a reconciled opening of zero.
    before_doc = await balances.find_one_and_update(
        identity,
        {
            "$inc": {"physical_qty": qty_delta, "version": 1},
            "$set": set_fields,
            "$setOnInsert": set_on_insert,
        },
        upsert=True,
        return_document=ReturnDocument.BEFORE,
        session=session,
    )
    created_balance = before_doc is None
    balance_before = int((before_doc or {}).get("physical_qty") or 0)
    balance_after = balance_before + qty_delta
    if _fail_after_balance:
        raise PhysicalStockAbortError("forced transaction abort")

    metadata = dict(requested.get("metadata") or {})
    prior_baseline = str((before_doc or {}).get("baseline_status") or "").strip()
    if created_balance and not establishes_reconciled_baseline:
        baseline_status = BASELINE_UNRECONCILED
    elif establishes_reconciled_baseline:
        baseline_status = BASELINE_RECONCILED
    elif prior_baseline == BASELINE_UNRECONCILED:
        baseline_status = BASELINE_UNRECONCILED
    else:
        baseline_status = prior_baseline or BASELINE_RECONCILED
    if baseline_status == BASELINE_UNRECONCILED:
        metadata["baseline_status"] = BASELINE_UNRECONCILED
        metadata["baseline_label"] = BASELINE_UNRECONCILED_LABEL
        if created_balance:
            metadata["baseline_created_by_outbound"] = True
    elif establishes_reconciled_baseline:
        metadata["baseline_status"] = BASELINE_RECONCILED

    doc = {
        **requested,
        "id": movement_id,
        "balance_before": balance_before,
        "balance_after": balance_after,
    }
    if metadata:
        doc["metadata"] = metadata
    await ledger.insert_one(doc, session=session)
    return _public_movement(doc)


async def _apply_once(db, requested: dict) -> dict:
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            return await _apply_movement_in_session(db, requested, session)


async def post_movement(
    db,
    raw: dict,
    *,
    actor_id: str,
) -> dict:
    """Append one physical movement and update its balance atomically."""
    await require_transaction_support(_client(db))
    movement_type = _as_text((raw or {}).get("movement_type"), "movement_type")
    if movement_type not in MOVEMENT_TYPES:
        raise PhysicalStockValidationError("Jenis movement tidak dikenal")
    qty_delta = _as_qty(
        (raw or {}).get("qty_delta"),
        allow_zero=movement_type == MOVEMENT_OPENING_BALANCE,
    )
    _check_sign(movement_type, qty_delta)
    occurred_at = str((raw or {}).get("occurred_at") or "").strip() or now_iso()
    created_at = now_iso()
    metadata = (raw or {}).get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise PhysicalStockValidationError("metadata harus objek")
    requested = {
        "master_item_id": _as_text((raw or {}).get("master_item_id"), "master_item_id"),
        "branch_id": _as_text((raw or {}).get("branch_id"), "branch_id"),
        "warehouse_id": _as_text((raw or {}).get("warehouse_id"), "warehouse_id"),
        "movement_type": movement_type,
        "qty_delta": qty_delta,
        "reference_type": str((raw or {}).get("reference_type") or "").strip(),
        "reference_id": str((raw or {}).get("reference_id") or "").strip(),
        "reason": str((raw or {}).get("reason") or "").strip(),
        "actor_id": _as_text(actor_id, "actor_id"),
        "occurred_at": occurred_at,
        "created_at": created_at,
        "idempotency_key": _as_text((raw or {}).get("idempotency_key"), "idempotency_key"),
    }
    if metadata:
        requested["metadata"] = metadata

    last_error: Optional[BaseException] = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            return await _apply_once(db, requested)
        except (PhysicalStockValidationError, PhysicalStockConflictError, InsufficientPhysicalStockError, PhysicalStockAbortError):
            raise
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    existing = await db[LEDGER_COLLECTION].find_one(
        {"idempotency_key": requested["idempotency_key"]},
        {"_id": 0},
    )
    if existing:
        if not _same_payload(existing, requested):
            raise PhysicalStockConflictError(
                "Idempotency key sudah digunakan dengan payload yang berbeda"
            )
        return _public_movement(existing)
    raise PhysicalStockUnavailableError("Transaksi stok fisik gagal setelah percobaan ulang") from last_error


def _delivery_line_key(row: dict) -> Tuple[int, str, int]:
    return (int(row["line_idx"]), str(row["master_item_id"]), int(row["qty"]))


def _same_delivery_confirmation(existing: dict, spec: dict) -> bool:
    if str(existing.get("order_id") or "") != spec["order_id"]:
        return False
    if str(existing.get("branch_id") or "") != spec["branch_id"]:
        return False
    if str(existing.get("warehouse_id") or "") != spec["warehouse_id"]:
        return False
    old = existing.get("lines") if isinstance(existing.get("lines"), list) else []
    new = spec["lines"]
    if len(old) != len(new):
        return False
    try:
        return sorted(_delivery_line_key(row) for row in old) == sorted(_delivery_line_key(row) for row in new)
    except (KeyError, TypeError, ValueError):
        return False


def _completed_qty_for_line(order: dict, line_idx: int) -> int:
    """Ready qty for delivery / Belum Terkirim (read-only; no order mutation).

    Print: operator completion. Non-print: full PO qty (siap kirim).
    """
    from quantity_rules import operational_ready_qty_for_line

    lines = list(order.get("line_items") or [])
    if line_idx < 0 or line_idx >= len(lines) or not isinstance(lines[line_idx], dict):
        raise PhysicalStockValidationError("Baris pesanan tidak ditemukan")
    return int(operational_ready_qty_for_line(order, line_idx))


def _resolve_requested_delivery_lines(order: dict, requested: List[dict]) -> List[dict]:
    lines = list(order.get("line_items") or [])
    if not requested:
        raise PhysicalStockValidationError("Minimal satu baris pengiriman")
    resolved = []
    seen = set()
    for raw in requested:
        if not isinstance(raw, dict):
            raise PhysicalStockValidationError("Baris pengiriman tidak valid")
        try:
            line_idx = int(raw.get("line_idx"))
            qty = int(raw.get("qty"))
        except (TypeError, ValueError):
            raise PhysicalStockValidationError("Baris pengiriman tidak valid")
        if isinstance(raw.get("qty"), bool) or qty <= 0:
            raise PhysicalStockValidationError("Kuantitas terkirim harus bilangan bulat positif")
        if line_idx in seen:
            raise PhysicalStockValidationError("Baris pengiriman duplikat")
        seen.add(line_idx)
        if line_idx < 0 or line_idx >= len(lines) or not isinstance(lines[line_idx], dict):
            raise PhysicalStockValidationError("Baris pesanan tidak ditemukan")
        po_line = lines[line_idx]
        master_item_id = str(po_line.get("master_item_id") or "").strip()
        if not master_item_id:
            raise PhysicalStockValidationError(
                "Baris pesanan tidak memiliki master_item_id sehingga stok fisik tidak dapat dikonfirmasi"
            )
        requested_master = str(raw.get("master_item_id") or "").strip()
        if requested_master and requested_master != master_item_id:
            raise PhysicalStockValidationError("master_item_id tidak cocok dengan baris pesanan")
        line_id = str(po_line.get("line_id") or raw.get("line_id") or line_idx)
        resolved.append({
            "line_idx": line_idx,
            "line_id": line_id,
            "master_item_id": master_item_id,
            "qty": qty,
        })
    return resolved


async def _resolve_delivery_location(db, session, *, branch_id: Optional[str], warehouse_id: Optional[str]) -> Tuple[str, str]:
    if warehouse_id:
        warehouse_id = _as_text(warehouse_id, "warehouse_id")
        warehouse = await db[WAREHOUSES_COLLECTION].find_one(
            {"id": warehouse_id},
            {"_id": 0, "id": 1, "branch_id": 1, "active": 1},
            session=session,
        )
        if not warehouse or warehouse.get("active") is not True:
            raise PhysicalStockValidationError("Gudang tidak aktif")
        resolved_branch = str(warehouse.get("branch_id") or "")
        if branch_id and str(branch_id).strip() != resolved_branch:
            raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")
        branch = await db[BRANCHES_COLLECTION].find_one(
            {"id": resolved_branch},
            {"_id": 0, "id": 1, "active": 1},
            session=session,
        )
        if not branch or branch.get("active") is not True:
            raise PhysicalStockValidationError("Cabang tidak aktif")
        return resolved_branch, warehouse_id

    branches = await db[BRANCHES_COLLECTION].find({}, {"_id": 0, "id": 1, "code": 1, "name": 1, "active": 1}, session=session).to_list(200)
    active = [row for row in branches if row.get("active") is True]
    operational = resolve_operational_branch(active)
    if not operational:
        raise PhysicalStockValidationError("Cabang operasional Yogyakarta tidak ditemukan")
    warehouses = await db[WAREHOUSES_COLLECTION].find(
        {"branch_id": operational["id"], "active": True},
        {"_id": 0, "id": 1},
        session=session,
    ).to_list(50)
    if len(warehouses) == 1 and warehouses[0].get("id"):
        return str(operational["id"]), str(warehouses[0]["id"])
    if not warehouses:
        raise PhysicalStockValidationError("Gudang aktif Yogyakarta tidak ditemukan")
    raise PhysicalStockValidationError("Pilih gudang sebelum konfirmasi terkirim")


async def _line_r2_delivered_qty(db, session, order_id: str, line_idx: int) -> int:
    """Net R2 delivered qty for one order line (SALES_DELIVERY minus reversals)."""
    rows = await db[LEDGER_COLLECTION].find(
        {
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "metadata.order_id": order_id,
            "metadata.line_idx": int(line_idx),
        },
        {"_id": 0, "id": 1, "qty_delta": 1},
        session=session,
    ).to_list(5000)
    total = 0
    for row in rows:
        delta = int(row.get("qty_delta") or 0)
        if delta >= 0:
            continue
        movement_id = str(row.get("id") or "")
        reversed_already = await db[LEDGER_COLLECTION].find_one(
            {
                "movement_type": MOVEMENT_REVERSAL,
                "reference_id": movement_id,
            },
            {"_id": 1},
            session=session,
        )
        if reversed_already:
            continue
        total += -delta
    return total


def _sales_delivery_request(
    *,
    line: dict,
    order_id: str,
    confirmation_id: str,
    branch_id: str,
    warehouse_id: str,
    actor_id: str,
    reason: str,
    sales_delivery_id: str = "",
) -> dict:
    created_at = now_iso()
    metadata = {
        "order_id": order_id,
        "line_idx": int(line["line_idx"]),
        "line_id": str(line["line_id"]),
        "confirmation_id": confirmation_id,
    }
    if sales_delivery_id:
        metadata["sales_delivery_id"] = sales_delivery_id
    return {
        "master_item_id": line["master_item_id"],
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "qty_delta": -int(line["qty"]),
        "reference_type": "sales_delivery_confirmation",
        "reference_id": confirmation_id,
        "reason": reason,
        "actor_id": actor_id,
        "occurred_at": created_at,
        "created_at": created_at,
        "idempotency_key": f"sales-delivery:{confirmation_id}:{line['line_idx']}:{line['master_item_id']}",
        "metadata": metadata,
    }


async def _load_delivery_movements(db, session, confirmation_id: str, lines: List[dict]) -> List[dict]:
    movements = []
    for line in lines:
        key = f"sales-delivery:{confirmation_id}:{line['line_idx']}:{line['master_item_id']}"
        existing = await _existing_movement(db, key, session)
        if not existing:
            raise PhysicalStockConflictError("Konfirmasi terkirim tersimpan tanpa movement stok fisik")
        movements.append(_public_movement(existing))
    return movements


async def apply_confirmed_sales_delivery(
    db,
    session,
    *,
    order: dict,
    confirmation_id: str,
    lines: List[dict],
    actor_id: str,
    warehouse_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    reason: str = "",
    write_order: bool = False,
    sales_delivery_id: Optional[str] = None,
) -> dict:
    """Post every SALES_DELIVERY line inside the caller's transaction.

    The caller persists delivery state in the same transaction. A failure here
    aborts that transaction, so the confirmation and the ledger commit together.
    """
    if not isinstance(order, dict) or not str(order.get("id") or "").strip():
        raise PhysicalStockValidationError("Pesanan tidak ditemukan")
    if _order_cancelled(order):
        raise PhysicalStockValidationError("Pesanan sudah dibatalkan")
    # Callers may invoke this inside an outer txn; authority must already be on.
    from services.r2_go_live import is_r2_authority_active
    if not await is_r2_authority_active(db):
        raise PhysicalStockValidationError(
            "Mutasi stok R2 belum diaktifkan. Aktifkan r2_authority_active sebelum menulis R2."
        )
    order_id = str(order["id"])
    confirmation_id = _as_text(confirmation_id, "confirmation_id")
    actor = _as_text(actor_id, "actor_id")
    note = str(reason or "").strip() or "Konfirmasi terkirim"
    sj_id = str(sales_delivery_id or "").strip()
    resolved = _resolve_requested_delivery_lines(order, lines)
    resolved_branch, resolved_warehouse = await _resolve_delivery_location(
        db, session, branch_id=branch_id, warehouse_id=warehouse_id,
    )
    spec = {
        "order_id": order_id,
        "branch_id": resolved_branch,
        "warehouse_id": resolved_warehouse,
        "lines": resolved,
    }
    existing = await db[SALES_DELIVERY_CONFIRMATIONS].find_one(
        {"id": confirmation_id},
        {"_id": 0},
        session=session,
    )
    if existing:
        if not _same_delivery_confirmation(existing, spec):
            raise PhysicalStockConflictError(
                "Konfirmasi terkirim sudah digunakan dengan payload yang berbeda"
            )
        movements = await _load_delivery_movements(db, session, confirmation_id, resolved)
        return {
            "confirmation_id": confirmation_id,
            "order_id": order_id,
            "branch_id": resolved_branch,
            "warehouse_id": resolved_warehouse,
            "replayed": True,
            "movements": movements,
            "stock_warnings": list(existing.get("stock_warnings") or []),
        }

    from services.r2_go_live import cutover_delivered_qty_for_order_line

    for line in resolved:
        completed = _completed_qty_for_line(order, line["line_idx"])
        delivered = await _line_r2_delivered_qty(db, session, order_id, line["line_idx"])
        cutover_done = int(await cutover_delivered_qty_for_order_line(db, order_id, line["line_idx"]))
        ready = max(completed - delivered - cutover_done, 0)
        if line["qty"] > ready:
            raise PhysicalStockValidationError(
                "Kuantitas terkirim melebihi Qty Belum Terkirim pada baris pesanan ini"
            )

    stock_warnings = []
    for line in resolved:
        bal = await db[BALANCE_COLLECTION].find_one(
            {
                "master_item_id": str(line["master_item_id"]),
                "branch_id": str(resolved_branch),
                "warehouse_id": str(resolved_warehouse),
            },
            {"_id": 0},
            session=session,
        )
        if bal is None:
            status = BASELINE_UNRECONCILED
            book = 0
            uncertain = True
        else:
            status = str(bal.get("baseline_status") or BASELINE_RECONCILED).strip() or BASELINE_RECONCILED
            book = int(_qty_num(bal.get("physical_qty")))
            uncertain = status == BASELINE_UNRECONCILED
        warning = build_stock_shortage_warning(
            master_item_id=line["master_item_id"],
            stok_tercatat=book,
            kebutuhan=line["qty"],
            baseline_status=status,
        )
        if uncertain:
            warning["stok_tercatat_uncertain"] = True
        stock_warnings.append(warning)

    movements = []
    for line in resolved:
        movements.append(await _apply_movement_in_session(
            db,
            _sales_delivery_request(
                line=line,
                order_id=order_id,
                confirmation_id=confirmation_id,
                branch_id=resolved_branch,
                warehouse_id=resolved_warehouse,
                actor_id=actor,
                reason=note,
                sales_delivery_id=sj_id,
            ),
            session,
        ))
    confirmation = {
        "id": confirmation_id,
        "order_id": order_id,
        "branch_id": resolved_branch,
        "warehouse_id": resolved_warehouse,
        "lines": resolved,
        "actor_id": actor,
        "reason": note,
        "sales_delivery_id": sj_id or None,
        "reversed": False,
        "stock_warnings": stock_warnings,
        "created_at": now_iso(),
    }
    await db[SALES_DELIVERY_CONFIRMATIONS].insert_one(confirmation, session=session)
    if write_order:
        marked = list(order.get("r2_confirmed_delivery_ids") or [])
        if confirmation_id not in marked:
            marked.append(confirmation_id)
        result = await db.orders.update_one(
            {"id": order_id},
            {"$set": {
                "r2_confirmed_delivery_ids": marked,
                "updated_at": now_iso(),
            }},
            session=session,
        )
        if result.matched_count == 0:
            raise PhysicalStockValidationError("Pesanan tidak ditemukan")
    if _fail_sales_delivery_persist:
        raise PhysicalStockAbortError("forced delivery persist abort")
    return {
        "confirmation_id": confirmation_id,
        "order_id": order_id,
        "branch_id": resolved_branch,
        "warehouse_id": resolved_warehouse,
        "replayed": False,
        "movements": movements,
        "stock_warnings": stock_warnings,
    }


async def confirm_sales_delivery(
    db,
    *,
    order_id: str,
    confirmation_id: str,
    lines: List[dict],
    actor_id: str,
    warehouse_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    reason: str = "",
) -> dict:
    """Confirm a physical delivery and post R2 stock in one transaction."""
    from services.r2_go_live import require_r2_authority_active

    await require_r2_authority_active(db)
    await require_transaction_support(_client(db))
    order_id = _as_text(order_id, "order_id")
    last_error: Optional[BaseException] = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            async with await _client(db).start_session() as session:
                async with session.start_transaction():
                    order = await db.orders.find_one({"id": order_id}, {"_id": 0}, session=session)
                    if not order:
                        raise PhysicalStockValidationError("Pesanan tidak ditemukan")
                    return await apply_confirmed_sales_delivery(
                        db,
                        session,
                        order=order,
                        confirmation_id=confirmation_id,
                        lines=lines,
                        actor_id=actor_id,
                        warehouse_id=warehouse_id,
                        branch_id=branch_id,
                        reason=reason,
                        write_order=True,
                    )
        except (PhysicalStockValidationError, PhysicalStockConflictError, InsufficientPhysicalStockError, PhysicalStockAbortError):
            raise
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    raise PhysicalStockUnavailableError("Transaksi pengiriman stok fisik gagal setelah percobaan ulang") from last_error


async def order_has_r2_balance(db, order: Optional[dict]) -> bool:
    """True when a sales line already has an R2 physical balance to protect."""
    if not isinstance(order, dict):
        return False
    master_ids = []
    for line in order.get("line_items") or []:
        if isinstance(line, dict):
            master_id = str(line.get("master_item_id") or "").strip()
            if master_id:
                master_ids.append(master_id)
    if not master_ids:
        return False
    found = await db[BALANCE_COLLECTION].count_documents({"master_item_id": {"$in": master_ids}})
    return found > 0


def build_stock_shortage_warning(
    *,
    master_item_id: str,
    stok_tercatat: Any,
    kebutuhan: Any,
    baseline_status: Optional[str] = None,
) -> dict:
    """Warning payload for shortage / unreconciled baseline — never a hard block."""
    book = int(_qty_num(stok_tercatat))
    need = int(_qty_num(kebutuhan))
    if need < 0:
        need = 0
    shortage = need - book
    if shortage < 0:
        shortage = 0
    status = str(baseline_status or BASELINE_RECONCILED).strip() or BASELINE_RECONCILED
    return {
        "master_item_id": str(master_item_id or ""),
        "stok_tercatat": book,
        "kebutuhan": need,
        "kekurangan": shortage,
        "baseline_status": status,
        "baseline_label": (
            BASELINE_UNRECONCILED_LABEL if status == BASELINE_UNRECONCILED else None
        ),
        "saldo_buku_negatif": book < 0,
        "blocks_delivery": False,
    }


async def require_r2_delivery_stock_ready(
    db,
    order: Optional[dict],
    *,
    warehouse_id: Optional[str],
    branch_id: Optional[str] = None,
    lines: Optional[List[dict]] = None,
) -> Tuple[str, str, List[dict]]:
    """Resolve delivery warehouse + master identity; read-only book qty warnings.

    Used by delivery-stock-preflight for R2 authority **or** warehouse
    standalone (R2 OFF). Never posts ledger/balance. Never blocks on book qty.

    Warehouse/master identity must be valid (no guessing). Missing balance is
    unreconciled baseline — delivery may proceed; warnings describe uncertainty.
    Zero book qty is valid and not a readiness failure.
    """
    if not isinstance(order, dict):
        raise PhysicalStockValidationError("Pesanan tidak ditemukan")
    wh_id = str(warehouse_id or "").strip()
    if not wh_id:
        raise PhysicalStockValidationError(
            "Pilih gudang sebelum konfirmasi terkirim saat stok gudang aktif"
        )
    warehouse = await _require_warehouse_any(db, wh_id)
    br_id = str(branch_id or warehouse.get("branch_id") or "").strip()
    if not br_id:
        raise PhysicalStockValidationError("Cabang gudang tidak valid")
    branch = await _require_branch_any(db, br_id)
    if str(warehouse.get("branch_id") or "") != str(branch.get("id") or ""):
        raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")

    source_lines = lines if lines is not None else (order.get("line_items") or [])
    warnings: List[dict] = []
    seen = set()
    for line in source_lines:
        if not isinstance(line, dict):
            continue
        mid = str(line.get("master_item_id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        await _require_master_item(db, mid)
        bal = await get_balance(db, mid, branch["id"], warehouse["id"])
        need = int(_qty_num(line.get("qty") if line.get("qty") is not None else line.get("quantity")))
        if bal is None:
            warnings.append(build_stock_shortage_warning(
                master_item_id=mid,
                stok_tercatat=0,
                kebutuhan=need,
                baseline_status=BASELINE_UNRECONCILED,
            ))
            # Do not claim physical is a reconciled zero — flag uncertainty only.
            warnings[-1]["stok_tercatat_uncertain"] = True
        else:
            status = str(bal.get("baseline_status") or BASELINE_RECONCILED).strip()
            warnings.append(build_stock_shortage_warning(
                master_item_id=mid,
                stok_tercatat=bal.get("physical_qty"),
                kebutuhan=need,
                baseline_status=status or BASELINE_RECONCILED,
            ))
    if not seen:
        raise PhysicalStockValidationError(
            "Baris pengiriman tidak dapat dipetakan ke master item untuk stok gudang"
        )
    return str(branch["id"]), str(warehouse["id"]), warnings


def _purchase_receipt_request(receipt: dict, line: dict, *, actor_id: str) -> dict:
    """Legacy sync builder — qty assumed already in stock base (PCS). Prefer async builder."""
    receipt_id = str(receipt.get("id") or "")
    line_id = str(line.get("line_id") or "")
    qty = line.get("received_qty")
    if isinstance(qty, bool) or (isinstance(qty, float) and not float(qty).is_integer()):
        raise PhysicalStockValidationError("Qty diterima harus bilangan bulat")
    if isinstance(qty, float):
        qty = int(qty)
    if not isinstance(qty, int) or qty <= 0:
        raise PhysicalStockValidationError("Qty diterima harus bilangan bulat")
    created_at = now_iso()
    return {
        "master_item_id": str(line.get("master_item_id") or ""),
        "branch_id": str(receipt.get("branch_id") or ""),
        "warehouse_id": str(receipt.get("warehouse_id") or ""),
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": qty,
        "reference_type": "purchase_goods_receipt",
        "reference_id": f"{receipt_id}:{line_id}",
        "reason": "Penerimaan pembelian",
        "actor_id": actor_id,
        "occurred_at": created_at,
        "created_at": created_at,
        "idempotency_key": f"purchase-receipt:{receipt_id}:{line_id}",
        "metadata": {
            "receipt_id": receipt_id,
            "line_id": line_id,
            "purchase_order_id": str(receipt.get("purchase_order_id") or ""),
            "purchase_order_line_id": str(line.get("purchase_order_line_id") or ""),
            "base_uom": "PCS",
            "base_qty": qty,
            "transaction_qty": qty,
            "transaction_uom": "PCS",
            "conversion_factor": 1,
        },
    }


async def build_purchase_receipt_movement_request(
    db, receipt: dict, line: dict, *, actor_id: str,
) -> dict:
    """Build GR→R2 movement with one-shot UOM snapshot (base_qty PCS).

    - If line already carries base_qty + base_uom (document converted once), post that.
    - Else if transaction_uom / unit is an alternate UOM, convert once via master setting.
    - Else treat received_qty as PCS (existing behavior — no silent re-conversion).
    Never converts twice.
    """
    from services.product_unit_conversion import (
        BASE_UOM_PCS,
        UnitConversionError,
        canonicalize_to_base,
        get_product_unit_setting,
        normalize_uom,
    )

    receipt_id = str(receipt.get("id") or "")
    line_id = str(line.get("line_id") or "")
    mid = str(line.get("master_item_id") or "")
    created_at = now_iso()
    setting = await get_product_unit_setting(db, mid) if mid else None
    master = await db[MASTER_ITEMS_COLLECTION].find_one({"id": mid}, {"_id": 0}) if mid else None

    try:
        if line.get("base_qty") is not None and normalize_uom(line.get("base_uom") or BASE_UOM_PCS):
            # Document already snapshotted — ledger uses base only.
            snap = canonicalize_to_base(
                transaction_qty=line.get("base_qty"),
                transaction_uom=normalize_uom(line.get("base_uom") or BASE_UOM_PCS),
                setting=setting,
                master=master,
                already_base=True,
            )
            # Preserve original transaction snapshot when present on the line.
            if line.get("transaction_qty") is not None and line.get("transaction_uom"):
                snap = {
                    **snap,
                    "transaction_qty": line.get("transaction_qty"),
                    "transaction_uom": normalize_uom(line.get("transaction_uom")),
                    "conversion_factor": line.get("conversion_factor")
                    if line.get("conversion_factor") is not None
                    else snap["conversion_factor"],
                    "base_qty": snap["base_qty"],
                    "base_uom": snap["base_uom"],
                }
        else:
            tx_uom = normalize_uom(
                line.get("transaction_uom") or line.get("unit") or BASE_UOM_PCS
            )
            tx_qty = line.get("received_qty")
            already_base = tx_uom == normalize_uom(
                (setting or {}).get("base_uom") or BASE_UOM_PCS
            )
            snap = canonicalize_to_base(
                transaction_qty=tx_qty,
                transaction_uom=tx_uom,
                setting=setting,
                master=master,
                already_base=already_base,
            )
    except UnitConversionError as exc:
        raise PhysicalStockValidationError(str(exc)) from exc

    qty = int(snap["base_qty"])
    if qty <= 0:
        raise PhysicalStockValidationError("Qty diterima (PCS) harus bilangan bulat > 0")

    return {
        "master_item_id": mid,
        "branch_id": str(receipt.get("branch_id") or ""),
        "warehouse_id": str(receipt.get("warehouse_id") or ""),
        "movement_type": MOVEMENT_PURCHASE_RECEIPT,
        "qty_delta": qty,
        "reference_type": "purchase_goods_receipt",
        "reference_id": f"{receipt_id}:{line_id}",
        "reason": "Penerimaan pembelian",
        "actor_id": actor_id,
        "occurred_at": created_at,
        "created_at": created_at,
        "idempotency_key": f"purchase-receipt:{receipt_id}:{line_id}",
        "metadata": {
            "receipt_id": receipt_id,
            "line_id": line_id,
            "purchase_order_id": str(receipt.get("purchase_order_id") or ""),
            "purchase_order_line_id": str(line.get("purchase_order_line_id") or ""),
            "transaction_qty": snap["transaction_qty"],
            "transaction_uom": snap["transaction_uom"],
            "conversion_factor": snap["conversion_factor"],
            "base_qty": snap["base_qty"],
            "base_uom": snap["base_uom"],
        },
    }


async def receipt_line_posts_physical_stock(db, line: dict) -> bool:
    """True when a GR line should mutate warehouse physical stock / HPP layers.

    Explicit SERVICE and NON_INVENTORY never post physical qty. Legacy masters
    with missing/blank item_type still post (inventory default — no silent
    reclassify). Document lines may still exist on the GR either way.
    """
    from services.inventory_accounting import is_physical_stock_item_type

    mid = str((line or {}).get("master_item_id") or "").strip()
    if not mid:
        return False
    master = await db[MASTER_ITEMS_COLLECTION].find_one({"id": mid}, {"_id": 0, "item_type": 1})
    return is_physical_stock_item_type((master or {}).get("item_type"))


async def apply_purchase_receipt_lines(db, session, receipt: dict, *, actor_id: str) -> List[dict]:
    """Post every PURCHASE_RECEIPT line inside the caller's transaction."""
    lines = receipt.get("lines") if isinstance(receipt.get("lines"), list) else []
    if not lines:
        raise PhysicalStockValidationError("Minimal satu baris penerimaan")
    movements = []
    for line in lines:
        if not await receipt_line_posts_physical_stock(db, line):
            continue
        movements.append(await _apply_movement_in_session(
            db,
            await build_purchase_receipt_movement_request(
                db, receipt, line, actor_id=actor_id,
            ),
            session,
        ))
    return movements


def _purchase_return_request(document: dict, line: dict, *, actor_id: str) -> dict:
    return_id = str(document.get("id") or "")
    line_id = str(line.get("line_id") or "")
    qty = line.get("return_qty")
    if isinstance(qty, bool) or (isinstance(qty, float) and not float(qty).is_integer()):
        raise PhysicalStockValidationError("Qty retur harus bilangan bulat")
    if isinstance(qty, float):
        qty = int(qty)
    if not isinstance(qty, int) or qty <= 0:
        raise PhysicalStockValidationError("Qty retur harus bilangan bulat")
    _check_sign(MOVEMENT_PURCHASE_RETURN, -qty)
    created_at = now_iso()
    return {
        "master_item_id": str(line.get("master_item_id") or ""),
        "branch_id": str(line.get("branch_id") or ""),
        "warehouse_id": str(line.get("warehouse_id") or ""),
        "movement_type": MOVEMENT_PURCHASE_RETURN,
        "qty_delta": -qty,
        "reference_type": "purchase_return",
        "reference_id": f"{return_id}:{line_id}",
        "reason": "Retur Pembelian",
        "actor_id": actor_id,
        "occurred_at": created_at,
        "created_at": created_at,
        "idempotency_key": f"purchase-return:{return_id}:{line_id}",
        "metadata": {
            "purchase_return_id": return_id,
            "purchase_return_line_id": line_id,
            "purchase_goods_receipt_id": str(line.get("purchase_goods_receipt_id") or ""),
            "purchase_goods_receipt_line_id": str(line.get("purchase_goods_receipt_line_id") or ""),
            "purchase_order_id": str(line.get("purchase_order_id") or document.get("purchase_order_id") or ""),
            "purchase_order_line_id": str(line.get("purchase_order_line_id") or ""),
        },
    }


def _stock_opname_adjustment_request(session_doc: dict, line: dict, *, actor_id: str, occurred_at: str) -> dict:
    session_id = str(session_doc.get("id") or "")
    line_id = str(line.get("line_id") or "")
    delta = int(line.get("difference_qty") or 0)
    _check_sign(MOVEMENT_STOCK_OPNAME_ADJUSTMENT, delta)
    book_at_posting = line.get("book_qty_at_posting")
    if book_at_posting is None:
        book_at_posting = line.get("book_qty") or 0
    metadata = {
        "stock_opname_session_id": session_id,
        "stock_opname_line_id": line_id,
        "opname_number": str(session_doc.get("opname_number") or ""),
        "mode": _opname_session_mode(session_doc),
        "book_qty": int(line.get("book_qty") or 0),
        "book_qty_at_posting": int(book_at_posting or 0),
        "physical_count_qty": int(line.get("physical_count_qty") or 0),
        "difference_qty": delta,
    }
    if line.get("physical_count_at"):
        metadata["physical_count_at"] = str(line.get("physical_count_at"))
    if line.get("net_movement_after_count") is not None:
        metadata["net_movement_after_count"] = int(line.get("net_movement_after_count") or 0)
    if line.get("target_qty") is not None:
        metadata["target_qty"] = int(line.get("target_qty") or 0)
    return {
        "master_item_id": str(line.get("master_item_id") or ""),
        "branch_id": str(session_doc.get("branch_id") or ""),
        "warehouse_id": str(session_doc.get("warehouse_id") or ""),
        "movement_type": MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        "qty_delta": delta,
        "reference_type": "stock_opname_session",
        "reference_id": session_id,
        "reason": "Penyesuaian Stock Opname",
        "actor_id": actor_id,
        "occurred_at": occurred_at,
        "created_at": occurred_at,
        "idempotency_key": f"stock-opname:{session_id}:{line_id}",
        "metadata": metadata,
    }


async def _reconcile_opname_zero_difference_in_session(
    db,
    mongo_session,
    session_doc: dict,
    line: dict,
    *,
    actor_id: str,
    occurred_at: str,
) -> Optional[dict]:
    """Reconcile unreconciled baseline when count equals book (incl. both zero).

    Writes a durable STOCK_OPNAME_ADJUSTMENT audit with qty_delta=0. Does not
    invent a balance row or change physical_qty. Already-reconciled lines are
    no-ops. Idempotent on stock-opname:{session_id}:{line_id}.
    """
    session_id = str(session_doc.get("id") or "").strip()
    identity = {
        "master_item_id": str(line.get("master_item_id") or "").strip(),
        "branch_id": str(session_doc.get("branch_id") or "").strip(),
        "warehouse_id": str(session_doc.get("warehouse_id") or "").strip(),
    }
    if not session_id or not all(identity.values()):
        return None

    requested = _stock_opname_adjustment_request(
        session_doc, line, actor_id=actor_id, occurred_at=occurred_at,
    )
    metadata = dict(requested.get("metadata") or {})
    metadata["reconcile_only"] = True
    metadata["baseline_status"] = BASELINE_RECONCILED
    requested["metadata"] = metadata

    existing = await _existing_movement(db, requested["idempotency_key"], mongo_session)
    if existing:
        if not _same_payload(existing, requested):
            raise PhysicalStockConflictError(
                "Idempotency key sudah digunakan dengan payload yang berbeda"
            )
        return _public_movement(existing)

    balances = db[BALANCE_COLLECTION]
    ledger = db[LEDGER_COLLECTION]
    current_doc = await balances.find_one(
        identity,
        {"_id": 0, "physical_qty": 1, "baseline_status": 1},
        session=mongo_session,
    )
    if current_doc is None:
        # Do not invent a zero opening / fictitious receipt.
        return None
    current = int(current_doc.get("physical_qty") or 0)
    prior_baseline = str(current_doc.get("baseline_status") or "").strip()
    if prior_baseline != BASELINE_UNRECONCILED:
        # Already settled — no unnecessary movement.
        return None

    await _guard_opname_freeze(
        db,
        mongo_session,
        warehouse_id=identity["warehouse_id"],
        movement_type=MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        allow_opname_session_id=session_id,
    )
    await _validate_masters(
        db,
        master_item_id=identity["master_item_id"],
        branch_id=identity["branch_id"],
        warehouse_id=identity["warehouse_id"],
        session=mongo_session,
    )

    movement_id = str(uuid.uuid4())
    metadata["target_physical_qty"] = current
    requested["metadata"] = metadata
    updated = await balances.find_one_and_update(
        {
            **identity,
            "physical_qty": current,
            "baseline_status": BASELINE_UNRECONCILED,
        },
        {
            "$set": {
                "baseline_status": BASELINE_RECONCILED,
                "last_movement_id": movement_id,
                "updated_at": occurred_at,
            },
            "$inc": {"version": 1},
        },
        upsert=False,
        return_document=ReturnDocument.BEFORE,
        session=mongo_session,
    )
    if not updated:
        raced = await _existing_movement(db, requested["idempotency_key"], mongo_session)
        if raced:
            if not _same_payload(raced, requested):
                raise PhysicalStockConflictError(
                    "Idempotency key sudah digunakan dengan payload yang berbeda"
                )
            return _public_movement(raced)
        fresh = await balances.find_one(
            identity,
            {"_id": 0, "physical_qty": 1, "baseline_status": 1},
            session=mongo_session,
        )
        if (
            fresh is not None
            and str(fresh.get("baseline_status") or "").strip() == BASELINE_RECONCILED
            and int(fresh.get("physical_qty") or 0) == current
        ):
            # Another reconciler won; no duplicate audit for this key.
            return None
        raise PhysicalStockRetryError("Saldo fisik berubah sebelum rekonsiliasi opname diterapkan")

    if _fail_after_balance:
        raise PhysicalStockAbortError("forced transaction abort")

    doc = {
        **requested,
        "id": movement_id,
        "balance_before": current,
        "balance_after": current,
    }
    await ledger.insert_one(doc, session=mongo_session)
    return _public_movement(doc)


async def apply_stock_opname_adjustments(
    db,
    mongo_session,
    session_doc: dict,
    lines: List[dict],
    *,
    actor_id: str,
    occurred_at: str,
) -> List[dict]:
    """Post STOCK_OPNAME_ADJUSTMENT rows for the owning active session.

    Non-zero differences post qty movements. Zero differences reconcile an
    unreconciled baseline with a qty_delta=0 audit row (count equals book,
    including both zero) — without inventing a qty change or balance row.
    """
    session_id = str(session_doc.get("id") or "")
    movements = []
    for line in lines:
        delta = int(line.get("difference_qty") or 0)
        if delta == 0:
            audit = await _reconcile_opname_zero_difference_in_session(
                db,
                mongo_session,
                session_doc,
                line,
                actor_id=actor_id,
                occurred_at=occurred_at,
            )
            if audit is not None:
                movements.append(audit)
            continue
        movements.append(await _apply_movement_in_session(
            db,
            _stock_opname_adjustment_request(
                session_doc, line, actor_id=actor_id, occurred_at=occurred_at,
            ),
            mongo_session,
            allow_opname_session_id=session_id,
        ))
    return movements


async def apply_purchase_return_lines(db, session, document: dict, *, actor_id: str) -> List[dict]:
    """Post every PURCHASE_RETURN line inside the caller's transaction. Draft returns never call this."""
    lines = document.get("lines") if isinstance(document.get("lines"), list) else []
    if not lines:
        raise PhysicalStockValidationError("Minimal satu baris retur")
    movements = []
    for line in lines:
        movements.append(await _apply_movement_in_session(
            db,
            _purchase_return_request(document, line, actor_id=actor_id),
            session,
        ))
    return movements


async def resolve_purchase_receipt_warehouse(
    db,
    session,
    *,
    warehouse_id: Optional[str],
) -> Tuple[str, str]:
    """Derive branch from the selected warehouse. Preselect only when one YGY warehouse is active."""
    if warehouse_id and str(warehouse_id).strip():
        return await _resolve_delivery_location(db, session, branch_id=None, warehouse_id=warehouse_id)
    branches = await db[BRANCHES_COLLECTION].find(
        {},
        {"_id": 0, "id": 1, "code": 1, "name": 1, "active": 1},
        session=session,
    ).to_list(200)
    operational = resolve_operational_branch([row for row in branches if row.get("active") is True])
    if not operational:
        raise PhysicalStockValidationError("Cabang operasional Yogyakarta tidak ditemukan")
    warehouses = await db[WAREHOUSES_COLLECTION].find(
        {"branch_id": operational["id"], "active": True},
        {"_id": 0, "id": 1},
        session=session,
    ).to_list(50)
    if len(warehouses) == 1 and warehouses[0].get("id"):
        return str(operational["id"]), str(warehouses[0]["id"])
    if not warehouses:
        raise PhysicalStockValidationError("Gudang aktif Yogyakarta tidak ditemukan")
    raise PhysicalStockValidationError("Pilih gudang sebelum penerimaan barang")


def _reversal_request(original: dict, *, actor_id: str, idempotency_key: str, reason: str) -> dict:
    delta = -int(original.get("qty_delta") or 0)
    if delta == 0:
        raise PhysicalStockValidationError("Movement asal tidak memiliki kuantitas")
    created_at = now_iso()
    return {
        "master_item_id": original.get("master_item_id"),
        "branch_id": original.get("branch_id"),
        "warehouse_id": original.get("warehouse_id"),
        "movement_type": MOVEMENT_REVERSAL,
        "qty_delta": delta,
        "reference_type": LEDGER_COLLECTION,
        "reference_id": original.get("id"),
        "reason": reason or f"Reversal {original.get('id')}",
        "actor_id": actor_id,
        "occurred_at": created_at,
        "created_at": created_at,
        "idempotency_key": idempotency_key,
        "metadata": {"reverses_movement_id": original.get("id")},
    }


_REVERSIBLE_MOVEMENT_TYPES = frozenset({
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_PURCHASE_RETURN,
    MOVEMENT_SALES_DELIVERY,
})


async def reverse_movement_in_session(
    db,
    session,
    movement_id: str,
    *,
    actor_id: str,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    """Append one REVERSAL against CURRENT book at cancel time.

    OWNER semantics: cancellation returns goods to the origin warehouse now.
    Intervening additive/reconcile movements stay intact; historical
    balance_before/after and opname counts are never rewritten. Active
    freeze-mode stock-opname still blocks via `_guard_opname_freeze`.
    """
    original = await db[LEDGER_COLLECTION].find_one(
        {"id": str(movement_id)},
        {"_id": 0},
        session=session,
    )
    if not original:
        raise PhysicalStockValidationError("Movement asal tidak ditemukan")
    if original.get("movement_type") == MOVEMENT_REVERSAL:
        raise PhysicalStockValidationError("Reversal tidak dapat direversal")
    if original.get("movement_type") not in _REVERSIBLE_MOVEMENT_TYPES:
        raise PhysicalStockValidationError(
            "Hanya penerimaan, retur pembelian, atau pengiriman R2 yang dapat dibatalkan pada jalur ini"
        )
    return await _apply_movement_in_session(
        db,
        _reversal_request(original, actor_id=actor_id, idempotency_key=idempotency_key, reason=reason),
        session,
    )


async def reverse_confirmed_sales_delivery(
    db,
    *,
    confirmation_id: str,
    actor_id: str,
    reason: str = "",
) -> dict:
    """Reverse every R2 SALES_DELIVERY posted for one confirmation. Idempotent.

    Pre-cutover historical deliveries have no R2 confirmation row / movements —
    those are a no-op (replayed with zero reversals).
    """
    confirmation_id = _as_text(confirmation_id, "confirmation_id")
    actor = _as_text(actor_id, "actor_id")
    note = str(reason or "").strip() or "Pembatalan konfirmasi terkirim R2"
    await require_transaction_support(_client(db))

    last_error: Optional[BaseException] = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            async with await _client(db).start_session() as session:
                async with session.start_transaction():
                    confirmation = await db[SALES_DELIVERY_CONFIRMATIONS].find_one(
                        {"id": confirmation_id},
                        {"_id": 0},
                        session=session,
                    )
                    if not confirmation:
                        return {
                            "confirmation_id": confirmation_id,
                            "replayed": True,
                            "reversed": False,
                            "reason": "confirmation_not_found",
                            "reversals": [],
                        }
                    if confirmation.get("reversed") is True:
                        return {
                            "confirmation_id": confirmation_id,
                            "replayed": True,
                            "reversed": True,
                            "reversals": list(confirmation.get("reversal_movement_ids") or []),
                        }
                    originals = await db[LEDGER_COLLECTION].find(
                        {
                            "movement_type": MOVEMENT_SALES_DELIVERY,
                            "metadata.confirmation_id": confirmation_id,
                        },
                        {"_id": 0, "id": 1},
                        session=session,
                    ).to_list(500)
                    reversal_ids = []
                    for original in originals:
                        movement = await reverse_movement_in_session(
                            db,
                            session,
                            original["id"],
                            actor_id=actor,
                            idempotency_key=f"sales-delivery-reversal:{confirmation_id}:{original['id']}",
                            reason=note,
                        )
                        reversal_ids.append(movement["id"])
                    await db[SALES_DELIVERY_CONFIRMATIONS].update_one(
                        {"id": confirmation_id, "reversed": {"$ne": True}},
                        {"$set": {
                            "reversed": True,
                            "reversed_at": now_iso(),
                            "reversed_by": actor,
                            "reversal_reason": note,
                            "reversal_movement_ids": reversal_ids,
                        }},
                        session=session,
                    )
                    return {
                        "confirmation_id": confirmation_id,
                        "replayed": False,
                        "reversed": True,
                        "reversals": reversal_ids,
                    }
        except (
            PhysicalStockValidationError,
            PhysicalStockConflictError,
            InsufficientPhysicalStockError,
            PhysicalStockAbortError,
        ):
            raise
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    raise PhysicalStockUnavailableError(
        "Transaksi pembatalan pengiriman stok fisik gagal setelah percobaan ulang"
    ) from last_error


def _sales_delivery_identity_tokens(delivery: dict) -> set:
    return {
        token
        for token in (
            str(delivery.get("id") or "").strip(),
            str(delivery.get("delivery_id") or "").strip(),
            str(delivery.get("sj_number") or "").strip(),
        )
        if token
    }


async def _r2_confirmation_ids_for_sales_delivery(db, delivery: dict, *, session=None) -> list:
    """Resolve physical R2 confirmation ids linked to one SJ document.

    Only identity-attributed confirmations are returned. PO-level
    ``r2_confirmed_delivery_ids`` without a matching ``sales_delivery_id``
    are intentionally excluded — callers that need fail-closed semantics
    must use ``assess_sales_delivery_cancel_stock``.
    """
    if not isinstance(delivery, dict):
        return []
    identity = _sales_delivery_identity_tokens(delivery)
    confirmation_ids = set()
    find_kwargs = {"session": session} if session is not None else {}
    if identity:
        linked = await db[SALES_DELIVERY_CONFIRMATIONS].find(
            {
                "$or": [
                    {"sales_delivery_id": {"$in": list(identity)}},
                    {"id": {"$in": list(identity)}},
                ]
            },
            {"_id": 0, "id": 1},
            **find_kwargs,
        ).to_list(200)
        for row in linked:
            if row.get("id"):
                confirmation_ids.add(str(row["id"]))
        rows = await db[LEDGER_COLLECTION].find(
            {
                "movement_type": MOVEMENT_SALES_DELIVERY,
                "metadata.sales_delivery_id": {"$in": list(identity)},
            },
            {"_id": 0, "metadata.confirmation_id": 1},
            **find_kwargs,
        ).to_list(500)
        for row in rows:
            cid = str((row.get("metadata") or {}).get("confirmation_id") or "").strip()
            if cid:
                confirmation_ids.add(cid)

    po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()
    if po_id and identity:
        # Attribute PO-listed confirmations only when sales_delivery_id matches
        # this SJ. Never inherit sibling-SJ confirmations.
        order = await db.orders.find_one(
            {"id": po_id},
            {"_id": 0, "r2_confirmed_delivery_ids": 1},
            **find_kwargs,
        )
        if order:
            listed = [
                str(cid).strip()
                for cid in (order.get("r2_confirmed_delivery_ids") or [])
                if str(cid or "").strip()
            ]
            if listed:
                rows = await db[SALES_DELIVERY_CONFIRMATIONS].find(
                    {"id": {"$in": listed}},
                    {"_id": 0, "id": 1, "sales_delivery_id": 1},
                    **find_kwargs,
                ).to_list(200)
                for row in rows:
                    sj = str(row.get("sales_delivery_id") or "").strip()
                    if sj and sj in identity and row.get("id"):
                        confirmation_ids.add(str(row["id"]))
    return sorted(confirmation_ids)


async def _ledger_row_is_reversed(db, movement_id: str, *, session=None) -> bool:
    mid = str(movement_id or "").strip()
    if not mid:
        return True
    find_kwargs = {"session": session} if session is not None else {}
    rev = await db[LEDGER_COLLECTION].find_one(
        {"movement_type": MOVEMENT_REVERSAL, "reference_id": mid},
        {"_id": 1},
        **find_kwargs,
    )
    return bool(rev)


async def assess_sales_delivery_cancel_stock(db, delivery: dict) -> dict:
    """Classify whether SJ cancel needs stock reversal, is operational-only, or must fail closed.

    Modes:
    - operational_only: no SJ-attributed stock mutation that must be reversed
    - requires_stock_reversal: R2/standalone ledger confirmations exist for this SJ
    - ambiguous: PO-level / unattributable delivery stock evidence
    - unsupported_stock: PSS/durable mutations attributed to this SJ (no safe cancel reversal here)
    """
    if not isinstance(delivery, dict) or not str(delivery.get("id") or "").strip():
        raise PhysicalStockValidationError("Surat Jalan tidak ditemukan")
    identity = _sales_delivery_identity_tokens(delivery)
    delivery_id = str(delivery["id"])
    po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()
    evidence: Dict[str, Any] = {
        "confirmation_ids": [],
        "active_ledger_ids": [],
        "pss_movement_ids": [],
        "pending_event_ids": [],
        "ambiguous_confirmation_ids": [],
        "orphan_ledger_ids": [],
    }

    confirmation_ids = await _r2_confirmation_ids_for_sales_delivery(db, delivery)
    active_confirmation_ids: List[str] = []
    for cid in confirmation_ids:
        conf = await db[SALES_DELIVERY_CONFIRMATIONS].find_one({"id": cid}, {"_id": 0, "reversed": 1})
        if conf and conf.get("reversed") is True:
            continue
        active_confirmation_ids.append(cid)
    evidence["confirmation_ids"] = list(active_confirmation_ids)

    if identity:
        ledger_rows = await db[LEDGER_COLLECTION].find(
            {
                "movement_type": MOVEMENT_SALES_DELIVERY,
                "metadata.sales_delivery_id": {"$in": list(identity)},
            },
            {"_id": 0, "id": 1},
        ).to_list(500)
        for row in ledger_rows:
            mid = str(row.get("id") or "").strip()
            if mid and not await _ledger_row_is_reversed(db, mid):
                evidence["active_ledger_ids"].append(mid)

    # Unattributable PO-listed R2 confirmations → fail closed (never reverse sibling SJ).
    if po_id:
        order = await db.orders.find_one(
            {"id": po_id},
            {
                "_id": 0,
                "r2_confirmed_delivery_ids": 1,
                "delivery_confirmations": 1,
            },
        ) or {}
        listed = [
            str(cid).strip()
            for cid in (order.get("r2_confirmed_delivery_ids") or [])
            if str(cid or "").strip()
        ]
        if listed:
            # Include reversed docs so a completed cancel is not treated as
            # "listed but missing" (that falsely fails closed on idempotent replay).
            rows = await db[SALES_DELIVERY_CONFIRMATIONS].find(
                {"id": {"$in": listed}},
                {"_id": 0, "id": 1, "sales_delivery_id": 1, "reversed": 1},
            ).to_list(200)
            known = {str(r.get("id")): r for r in rows if r.get("id")}
            for cid in listed:
                row = known.get(cid)
                if not row:
                    # Listed but missing doc — cannot prove attribution.
                    evidence["ambiguous_confirmation_ids"].append(cid)
                    continue
                if row.get("reversed") is True:
                    continue  # already reversed — not an active cancel blocker
                sj = str(row.get("sales_delivery_id") or "").strip()
                if not sj:
                    evidence["ambiguous_confirmation_ids"].append(cid)
                elif identity and sj not in identity:
                    continue  # sibling SJ — independent
                elif identity and sj in identity and cid not in evidence["confirmation_ids"]:
                    evidence["confirmation_ids"].append(cid)
                    if cid not in active_confirmation_ids:
                        active_confirmation_ids.append(cid)

        # Orphan R2 ledger on this PO without sales_delivery_id attribution.
        orphan_rows = await db[LEDGER_COLLECTION].find(
            {
                "movement_type": MOVEMENT_SALES_DELIVERY,
                "metadata.order_id": po_id,
                "$or": [
                    {"metadata.sales_delivery_id": {"$exists": False}},
                    {"metadata.sales_delivery_id": None},
                    {"metadata.sales_delivery_id": ""},
                ],
            },
            {"_id": 0, "id": 1, "metadata.confirmation_id": 1},
        ).to_list(500)
        for row in orphan_rows:
            mid = str(row.get("id") or "").strip()
            if not mid or await _ledger_row_is_reversed(db, mid):
                continue
            evidence["orphan_ledger_ids"].append(mid)

        # Delivery-confirmed operational rows linked to this SJ may have PSS stock-out.
        linked_conf_ids = []
        for rec in order.get("delivery_confirmations") or []:
            if not isinstance(rec, dict):
                continue
            if rec.get("is_deleted") or rec.get("deleted") or rec.get("is_cancelled") or rec.get("cancelled"):
                continue
            linked = False
            for field in ("shipment_id", "surat_jalan_id", "sj_number", "sales_delivery_id", "delivery_id"):
                token = str(rec.get(field) or "").strip()
                if token and token in identity:
                    linked = True
                    break
            if linked:
                cid = str(rec.get("id") or rec.get("action_id") or "").strip()
                if cid:
                    linked_conf_ids.append(cid)

        pss_query_or = [
            {"reference_type": "sales_delivery", "reference_id": {"$in": list(identity)}},
            {"event_key": {"$regex": f"^stock_out:sales_delivery:{re.escape(delivery_id)}:"}},
        ]
        for cid in linked_conf_ids:
            pss_query_or.append(
                {"event_key": {"$regex": f"^stock_out:delivery:{re.escape(po_id)}:{re.escape(cid)}:"}}
            )
        pss_rows = await db.stock_movements.find(
            {
                "$or": pss_query_or,
                "status": {"$nin": ["VOID", "REVERSED", "CANCELLED", "void", "reversed", "cancelled"]},
            },
            {"_id": 0, "id": 1, "event_key": 1, "status": 1},
        ).to_list(500)
        for row in pss_rows:
            evidence["pss_movement_ids"].append(str(row.get("id") or row.get("event_key") or ""))

        # In-flight durable claims attributed to this SJ.
        try:
            from services.product_stock_state import COLLECTION as STATE_COLLECTION
        except Exception:  # pragma: no cover - import isolation
            STATE_COLLECTION = "product_stock_state"
        pending_or = [
            {"pending_event.event_id": {"$regex": f"^stock_out:sales_delivery:{re.escape(delivery_id)}:"}},
            {"pending_event.reference_type": "sales_delivery", "pending_event.reference_id": {"$in": list(identity)}},
        ]
        for cid in linked_conf_ids:
            pending_or.append({
                "pending_event.event_id": {
                    "$regex": f"^stock_out:delivery:{re.escape(po_id)}:{re.escape(cid)}:"
                },
            })
        pending_rows = await db[STATE_COLLECTION].find(
            {"$or": pending_or},
            {"_id": 0, "pending_event.event_id": 1},
        ).to_list(200)
        for row in pending_rows:
            ev = str(((row.get("pending_event") or {}).get("event_id")) or "").strip()
            if ev:
                evidence["pending_event_ids"].append(ev)

    if evidence["ambiguous_confirmation_ids"] or evidence["orphan_ledger_ids"]:
        return {
            "mode": "ambiguous",
            "reason": (
                "Mutasi stok pengiriman pada PO tidak dapat diatribusikan ke Surat Jalan ini "
                "secara unik. Pembatalan ditolak (fail closed)."
            ),
            "evidence": evidence,
        }
    if evidence["pss_movement_ids"] or evidence["pending_event_ids"]:
        return {
            "mode": "unsupported_stock",
            "reason": (
                "Surat Jalan memiliki mutasi stok PSS/durable aktif yang belum dapat "
                "dibalikkan secara aman melalui pembatalan ini."
            ),
            "evidence": evidence,
        }
    if evidence["active_ledger_ids"] and not evidence["confirmation_ids"]:
        return {
            "mode": "unsupported_stock",
            "reason": (
                "Surat Jalan memiliki mutasi ledger stok aktif tanpa konfirmasi R2 "
                "yang dapat di-reversal secara aman. Pembatalan ditolak."
            ),
            "evidence": evidence,
        }
    if evidence["confirmation_ids"]:
        return {
            "mode": "requires_stock_reversal",
            "reason": "Surat Jalan memiliki mutasi stok R2/warehouse yang harus di-reversal.",
            "evidence": evidence,
            "confirmation_ids": list(evidence["confirmation_ids"]),
        }
    return {
        "mode": "operational_only",
        "reason": "Tidak ada mutasi stok yang perlu dibalik untuk Surat Jalan ini.",
        "evidence": evidence,
        "confirmation_ids": [],
    }


def _order_soft_cancel_patch_for_delivery(
    order: dict,
    *,
    identity: set,
    delivery_id: str,
    cancelled_at: str,
) -> dict:
    """Build order $set patch for soft-cancelling one SJ. Loading is never touched."""
    shipments = []
    for sh in order.get("shipments") or []:
        if not isinstance(sh, dict):
            continue
        row = dict(sh)
        if _shipment_matches_delivery(row, identity):
            row["is_cancelled"] = True
            row["cancelled"] = True
            row["status"] = "cancelled"
            row["sj_status"] = "cancelled"
            row["cancelled_at"] = cancelled_at
        shipments.append(row)
    confirmations = []
    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict):
            continue
        row = dict(rec)
        linked = False
        for field in (
            "shipment_id", "surat_jalan_id", "sj_number",
            "sales_delivery_id", "delivery_id",
        ):
            token = str(row.get(field) or "").strip()
            if token and token in identity:
                linked = True
                break
        if linked:
            row["is_cancelled"] = True
            row["cancelled"] = True
            row["cancelled_at"] = cancelled_at
        confirmations.append(row)
    files = []
    for entry in order.get("surat_jalan_files") or []:
        if not isinstance(entry, dict):
            continue
        file_row = dict(entry)
        if str(file_row.get("source") or "") == "SYSTEM_GENERATED" and (
            str(file_row.get("id") or "") == delivery_id
            or str(file_row.get("shipment_id") or "") == delivery_id
        ):
            file_row["cancelled"] = True
            file_row["status"] = "cancelled"
            file_row["cancelled_at"] = cancelled_at
        files.append(file_row)
    order_view = dict(order)
    order_view["shipments"] = shipments
    order_view["surat_jalan_files"] = files
    order_view["delivery_confirmations"] = confirmations
    recomputed = _recompute_order_delivery_after_cancel(order_view)
    return {
        "shipments": shipments,
        "surat_jalan_files": files,
        "delivery_confirmations": confirmations,
        "qty_delivered": recomputed["qty_delivered"],
        "delivery_status": recomputed["delivery_status"],
        "current_stage": recomputed["current_stage"],
        "updated_at": cancelled_at,
    }


async def cancel_sales_delivery_document_operational(
    db,
    delivery: dict,
    *,
    actor_id: str,
    reason: str = "",
) -> dict:
    """Soft-cancel SJ + reconcile order without stock writes or multi-doc transactions.

    Safe on MongoDB standalone when assess_sales_delivery_cancel_stock reports
    operational_only. Loading confirmations are never auto-cancelled.

    Recovery / idempotency: SJ cancel is written first (conditional). Replays
    always re-apply the order patch so a crash between the two writes heals.
    """
    if not isinstance(delivery, dict) or not str(delivery.get("id") or "").strip():
        raise PhysicalStockValidationError("Surat Jalan tidak ditemukan")
    delivery_id = str(delivery["id"])
    actor = _as_text(actor_id, "actor_id")
    note = str(reason or "").strip() or "Pembatalan Surat Jalan"
    identity = _sales_delivery_identity_tokens(delivery)
    po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()

    fresh = await db.sales_deliveries.find_one({"id": delivery_id}, {"_id": 0})
    if not fresh:
        raise PhysicalStockNotFoundError("Pengiriman Pesanan tidak ditemukan.")

    already = bool(
        fresh.get("is_cancelled") is True
        or str(fresh.get("status") or "").lower() in {"cancelled", "dibatalkan", "deleted"}
    )
    cancelled_at = str(fresh.get("cancelled_at") or "").strip() or now_iso()
    replayed = already

    if not already:
        cancelled_at = now_iso()
        result = await db.sales_deliveries.update_one(
            {
                "id": delivery_id,
                "is_cancelled": {"$ne": True},
                "status": {"$nin": ["DIBATALKAN", "CANCELLED", "DELETED", "cancelled", "dibatalkan", "deleted"]},
            },
            {"$set": {
                "is_cancelled": True,
                "cancelled": True,
                "status": "DIBATALKAN",
                "cancelled_at": cancelled_at,
                "cancel_reason": note,
                "cancelled_by": actor,
                "r2_reversal": {
                    "reversed_confirmations": [],
                    "confirmation_ids": [],
                    "engine": "operational_no_stock",
                },
                "updated_at": cancelled_at,
            }},
        )
        if result.matched_count == 0:
            # Concurrent cancel won — treat as replay and heal order.
            replayed = True
            fresh = await db.sales_deliveries.find_one({"id": delivery_id}, {"_id": 0}) or fresh
            cancelled_at = str(fresh.get("cancelled_at") or cancelled_at)
        else:
            replayed = False

    order_patch: Dict[str, Any] = {}
    if po_id:
        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
        if order:
            order_patch = _order_soft_cancel_patch_for_delivery(
                order,
                identity=identity,
                delivery_id=delivery_id,
                cancelled_at=cancelled_at,
            )
            await db.orders.update_one({"id": po_id}, {"$set": order_patch})

    return {
        "id": delivery_id,
        "sj_number": fresh.get("sj_number", delivery_id),
        "soft_cancelled": True,
        "deleted": False,
        "replayed": replayed,
        "r2_reversal": {"reversed_confirmations": [], "confirmation_ids": []},
        "order_id": po_id or None,
        "qty_delivered": order_patch.get("qty_delivered"),
        "delivery_status": order_patch.get("delivery_status"),
        "current_stage": order_patch.get("current_stage"),
        "engine": "operational_no_stock",
    }


def _shipment_matches_delivery(shipment: dict, identity: set) -> bool:
    if not isinstance(shipment, dict) or not identity:
        return False
    for field in ("id", "sales_delivery_id", "sj_number", "number"):
        token = str(shipment.get(field) or "").strip()
        if token and token in identity:
            return True
    return False


def _recompute_order_delivery_after_cancel(order: dict) -> dict:
    """Recompute qty_delivered / delivery_status / stage from active shipments."""
    from quantity_rules import (
        delivery_batch_qty,
        packing_required_total,
        qty_delivered_for_shipment,
        shipment_is_active,
    )

    delivered = 0
    for sh in order.get("shipments") or []:
        if not isinstance(sh, dict) or not shipment_is_active(sh):
            continue
        delivered += max(0, int(qty_delivered_for_shipment(order, sh) or 0))
    required = max(0, int(packing_required_total(order) or 0))
    if required <= 0:
        required = sum(
            max(0, int((ln or {}).get("quantity") or 0))
            for ln in (order.get("line_items") or [])
            if isinstance(ln, dict)
        )
    delivered = min(delivered, required) if required > 0 else delivered
    if delivered <= 0:
        status = "Belum Terkirim"
    elif required > 0 and delivered >= required:
        status = "Terkirim"
    else:
        status = "Sebagian Terkirim"

    stage = int(order.get("current_stage") or 0)
    if status == "Terkirim":
        stage = max(stage, 14)
    elif status == "Sebagian Terkirim":
        stage = min(max(stage, 13), 14)
    else:
        # No active delivered qty — leave Loading/SJ stage, not selesai.
        if stage >= 14:
            stage = 13

    return {
        "qty_delivered": delivered,
        "delivery_status": status,
        "current_stage": stage,
        "required_qty": required,
        "active_batch_qty": sum(
            delivery_batch_qty(sh)
            for sh in (order.get("shipments") or [])
            if isinstance(sh, dict) and shipment_is_active(sh)
        ),
    }


async def reverse_r2_deliveries_for_sales_delivery(
    db,
    delivery: dict,
    *,
    actor_id: str,
    reason: str = "",
) -> dict:
    """Reverse R2 confirmations linked to a sales_delivery / SJ document.

    Legacy SJ with no R2 confirmation movements → no-op.
    """
    if not isinstance(delivery, dict):
        return {"reversed_confirmations": [], "replayed": True}
    confirmation_ids = await _r2_confirmation_ids_for_sales_delivery(db, delivery)
    results = []
    for cid in confirmation_ids:
        results.append(
            await reverse_confirmed_sales_delivery(
                db, confirmation_id=cid, actor_id=actor_id, reason=reason,
            )
        )
    return {
        "reversed_confirmations": results,
        "confirmation_ids": confirmation_ids,
    }


async def cancel_sales_delivery_document(
    db,
    delivery: dict,
    *,
    actor_id: str,
    reason: str = "",
) -> dict:
    """Option B: cancel SJ + reverse R2 + reconcile order in one transaction.

    Append-only REVERSAL at cancellation time against current book (goods return
    now). Intervening ledger rows are preserved; no retroactive restatement.
    Original SALES_DELIVERY, proofs, and signatures retained. Idempotent when
    SJ already cancelled. Freeze-mode active opname still fail-closed.
    """
    if not isinstance(delivery, dict) or not str(delivery.get("id") or "").strip():
        raise PhysicalStockValidationError("Surat Jalan tidak ditemukan")
    delivery_id = str(delivery["id"])
    actor = _as_text(actor_id, "actor_id")
    note = str(reason or "").strip() or "Pembatalan Surat Jalan"
    await require_transaction_support(_client(db))
    identity = _sales_delivery_identity_tokens(delivery)
    po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()

    last_error: Optional[BaseException] = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            async with await _client(db).start_session() as session:
                async with session.start_transaction():
                    fresh = await db.sales_deliveries.find_one(
                        {"id": delivery_id}, {"_id": 0}, session=session,
                    )
                    if not fresh:
                        raise PhysicalStockNotFoundError("Pengiriman Pesanan tidak ditemukan.")
                    if fresh.get("is_cancelled") is True or str(fresh.get("status") or "").lower() in (
                        "cancelled", "dibatalkan", "deleted",
                    ):
                        order = await db.orders.find_one({"id": po_id}, {"_id": 0}, session=session) if po_id else None
                        return {
                            "id": delivery_id,
                            "sj_number": fresh.get("sj_number", delivery_id),
                            "soft_cancelled": True,
                            "deleted": False,
                            "replayed": True,
                            "r2_reversal": {"reversed_confirmations": [], "confirmation_ids": []},
                            "order_id": po_id or None,
                            "qty_delivered": (order or {}).get("qty_delivered"),
                            "delivery_status": (order or {}).get("delivery_status"),
                        }

                    confirmation_ids = await _r2_confirmation_ids_for_sales_delivery(
                        db, fresh, session=session,
                    )
                    reversal_results = []
                    all_reversal_ids = []
                    for cid in confirmation_ids:
                        confirmation = await db[SALES_DELIVERY_CONFIRMATIONS].find_one(
                            {"id": cid}, {"_id": 0}, session=session,
                        )
                        if not confirmation:
                            reversal_results.append({
                                "confirmation_id": cid,
                                "replayed": True,
                                "reversed": False,
                                "reason": "confirmation_not_found",
                                "reversals": [],
                            })
                            continue
                        if confirmation.get("reversed") is True:
                            ids = list(confirmation.get("reversal_movement_ids") or [])
                            reversal_results.append({
                                "confirmation_id": cid,
                                "replayed": True,
                                "reversed": True,
                                "reversals": ids,
                            })
                            all_reversal_ids.extend(ids)
                            continue
                        originals = await db[LEDGER_COLLECTION].find(
                            {
                                "movement_type": MOVEMENT_SALES_DELIVERY,
                                "metadata.confirmation_id": cid,
                            },
                            {"_id": 0, "id": 1},
                            session=session,
                        ).to_list(500)
                        reversal_ids = []
                        for original in originals:
                            movement = await reverse_movement_in_session(
                                db,
                                session,
                                original["id"],
                                actor_id=actor,
                                idempotency_key=f"sales-delivery-reversal:{cid}:{original['id']}",
                                reason=note,
                            )
                            reversal_ids.append(movement["id"])
                        await db[SALES_DELIVERY_CONFIRMATIONS].update_one(
                            {"id": cid, "reversed": {"$ne": True}},
                            {"$set": {
                                "reversed": True,
                                "reversed_at": now_iso(),
                                "reversed_by": actor,
                                "reversal_reason": note,
                                "reversal_movement_ids": reversal_ids,
                            }},
                            session=session,
                        )
                        reversal_results.append({
                            "confirmation_id": cid,
                            "replayed": False,
                            "reversed": True,
                            "reversals": reversal_ids,
                        })
                        all_reversal_ids.extend(reversal_ids)

                    cancelled_at = now_iso()
                    await db.sales_deliveries.update_one(
                        {"id": delivery_id},
                        {"$set": {
                            "is_cancelled": True,
                            "cancelled": True,
                            "status": "DIBATALKAN",
                            "cancelled_at": cancelled_at,
                            "cancel_reason": note,
                            "r2_reversal": {
                                "reversed_confirmations": reversal_results,
                                "confirmation_ids": confirmation_ids,
                            },
                            "updated_at": cancelled_at,
                        }},
                        session=session,
                    )

                    order_patch = {}
                    if po_id:
                        order = await db.orders.find_one({"id": po_id}, {"_id": 0}, session=session)
                        if order:
                            shipments = []
                            for sh in order.get("shipments") or []:
                                if not isinstance(sh, dict):
                                    continue
                                row = dict(sh)
                                if _shipment_matches_delivery(row, identity):
                                    row["is_cancelled"] = True
                                    row["cancelled"] = True
                                    row["status"] = "cancelled"
                                    row["sj_status"] = "cancelled"
                                    row["cancelled_at"] = cancelled_at
                                    # Keep delivered_at / proofs linkage for audit; inactive via flags.
                                shipments.append(row)
                            confirmations = []
                            for rec in order.get("delivery_confirmations") or []:
                                if not isinstance(rec, dict):
                                    continue
                                row = dict(rec)
                                linked = False
                                for field in (
                                    "shipment_id", "surat_jalan_id", "sj_number",
                                    "sales_delivery_id", "delivery_id",
                                ):
                                    token = str(row.get(field) or "").strip()
                                    if token and token in identity:
                                        linked = True
                                        break
                                if linked:
                                    row["is_cancelled"] = True
                                    row["cancelled"] = True
                                    row["cancelled_at"] = cancelled_at
                                confirmations.append(row)
                            files = []
                            for entry in order.get("surat_jalan_files") or []:
                                if not isinstance(entry, dict):
                                    continue
                                file_row = dict(entry)
                                # Keep the document for audit, but stop treating the cancelled SJ as live.
                                if str(file_row.get("source") or "") == "SYSTEM_GENERATED" and (
                                    str(file_row.get("id") or "") == delivery_id
                                    or str(file_row.get("shipment_id") or "") == delivery_id
                                ):
                                    file_row["cancelled"] = True
                                    file_row["status"] = "cancelled"
                                    file_row["cancelled_at"] = cancelled_at
                                files.append(file_row)
                            order["shipments"] = shipments
                            order["surat_jalan_files"] = files
                            order["delivery_confirmations"] = confirmations
                            recomputed = _recompute_order_delivery_after_cancel(order)
                            order_patch = {
                                "shipments": shipments,
                                "surat_jalan_files": files,
                                "delivery_confirmations": confirmations,
                                "qty_delivered": recomputed["qty_delivered"],
                                "delivery_status": recomputed["delivery_status"],
                                "current_stage": recomputed["current_stage"],
                                "updated_at": cancelled_at,
                            }
                            await db.orders.update_one(
                                {"id": po_id},
                                {"$set": order_patch},
                                session=session,
                            )

                    return {
                        "id": delivery_id,
                        "sj_number": fresh.get("sj_number", delivery_id),
                        "soft_cancelled": True,
                        "deleted": False,
                        "replayed": False,
                        "r2_reversal": {
                            "reversed_confirmations": reversal_results,
                            "confirmation_ids": confirmation_ids,
                            "reversal_movement_ids": all_reversal_ids,
                        },
                        "order_id": po_id or None,
                        "qty_delivered": order_patch.get("qty_delivered"),
                        "delivery_status": order_patch.get("delivery_status"),
                        "current_stage": order_patch.get("current_stage"),
                    }
        except (
            PhysicalStockValidationError,
            PhysicalStockConflictError,
            InsufficientPhysicalStockError,
            PhysicalStockAbortError,
            PhysicalStockNotFoundError,
        ):
            raise
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    raise PhysicalStockUnavailableError(
        "Transaksi pembatalan Surat Jalan gagal setelah percobaan ulang"
    ) from last_error


async def reverse_movement(
    db,
    movement_id: str,
    *,
    actor_id: str,
    idempotency_key: str,
    reason: str = "",
) -> dict:
    """Append a reversal. The original ledger row is never edited or deleted."""
    original = await db[LEDGER_COLLECTION].find_one({"id": str(movement_id)}, {"_id": 0})
    if not original:
        raise PhysicalStockValidationError("Movement asal tidak ditemukan")
    if original.get("movement_type") == MOVEMENT_REVERSAL:
        raise PhysicalStockValidationError("Reversal tidak dapat direversal")
    delta = -int(original.get("qty_delta") or 0)
    return await post_movement(
        db,
        {
            "master_item_id": original.get("master_item_id"),
            "branch_id": original.get("branch_id"),
            "warehouse_id": original.get("warehouse_id"),
            "movement_type": MOVEMENT_REVERSAL,
            "qty_delta": delta,
            "reference_type": LEDGER_COLLECTION,
            "reference_id": original.get("id"),
            "reason": reason or f"Reversal {original.get('id')}",
            "idempotency_key": idempotency_key,
            "metadata": {"reverses_movement_id": original.get("id")},
        },
        actor_id=actor_id,
    )


async def get_balance(db, master_item_id: str, branch_id: str, warehouse_id: str) -> Optional[dict]:
    doc = await db[BALANCE_COLLECTION].find_one(
        {
            "master_item_id": str(master_item_id),
            "branch_id": str(branch_id),
            "warehouse_id": str(warehouse_id),
        },
        {"_id": 0},
    )
    return doc


async def reconcile_balance(db, master_item_id: str, branch_id: str, warehouse_id: str) -> dict:
    """Compare the stored balance with the sum of ledger qty_delta. No scheduler."""
    identity = {
        "master_item_id": str(master_item_id),
        "branch_id": str(branch_id),
        "warehouse_id": str(warehouse_id),
    }
    grouped = await db[LEDGER_COLLECTION].aggregate([
        {"$match": identity},
        {"$group": {"_id": None, "qty": {"$sum": "$qty_delta"}}},
    ]).to_list(1)
    ledger_qty = int(grouped[0]["qty"]) if grouped else 0
    stored = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0, "physical_qty": 1})
    balance_qty = int(stored.get("physical_qty") or 0) if stored else 0
    return {
        "master_item_id": identity["master_item_id"],
        "branch_id": identity["branch_id"],
        "warehouse_id": identity["warehouse_id"],
        "ledger_qty": ledger_qty,
        "balance_qty": balance_qty,
        "match": ledger_qty == balance_qty,
        "discrepancy": balance_qty - ledger_qty,
    }


UNIT_SETTINGS_COLLECTION = "product_unit_settings"
_MAX_READ_LIMIT = 200


async def _require_master_item(db, item_id: str) -> dict:
    doc = await db[MASTER_ITEMS_COLLECTION].find_one({"id": str(item_id)}, {"_id": 0})
    if not doc:
        raise PhysicalStockNotFoundError("Barang tidak ditemukan")
    return doc


async def _require_branch_any(db, branch_id: str) -> dict:
    doc = await db[BRANCHES_COLLECTION].find_one({"id": str(branch_id)}, {"_id": 0})
    if not doc:
        raise PhysicalStockNotFoundError("Cabang tidak ditemukan")
    return doc


async def _require_warehouse_any(db, warehouse_id: str) -> dict:
    doc = await db[WAREHOUSES_COLLECTION].find_one({"id": str(warehouse_id)}, {"_id": 0})
    if not doc:
        raise PhysicalStockNotFoundError("Gudang tidak ditemukan")
    return doc


async def _base_unit(db, master: dict) -> str:
    setting = await db[UNIT_SETTINGS_COLLECTION].find_one(
        {"master_item_id": master.get("id")},
        {"_id": 0, "base_uom": 1},
    )
    raw = ""
    if setting and setting.get("base_uom"):
        raw = setting.get("base_uom")
    elif master.get("unit"):
        raw = master.get("unit")
    text = str(raw or "PCS").strip().upper()
    return text or "PCS"


def _location_fields(branch: Optional[dict], warehouse: Optional[dict]) -> dict:
    branch = branch or {}
    warehouse = warehouse or {}
    return {
        "branch_id": branch.get("id") or "",
        "branch_code": branch.get("code") or "",
        "branch_name": branch.get("name") or "",
        "warehouse_id": warehouse.get("id") or "",
        "warehouse_code": warehouse.get("code") or "",
        "warehouse_name": warehouse.get("name") or "",
    }


async def _location_maps(db, branch_ids, warehouse_ids):
    branches = {}
    warehouses = {}
    if branch_ids:
        rows = await db[BRANCHES_COLLECTION].find(
            {"id": {"$in": list(branch_ids)}},
            {"_id": 0},
        ).to_list(len(branch_ids))
        branches = {row["id"]: row for row in rows}
    if warehouse_ids:
        rows = await db[WAREHOUSES_COLLECTION].find(
            {"id": {"$in": list(warehouse_ids)}},
            {"_id": 0},
        ).to_list(len(warehouse_ids))
        warehouses = {row["id"]: row for row in rows}
    return branches, warehouses


def _stock_row(master_item_id: str, branch: Optional[dict], warehouse: Optional[dict], qty, base_unit: str) -> dict:
    row = {
        "master_item_id": master_item_id,
        "physical_qty": int(qty or 0),
        "base_unit": base_unit,
    }
    row.update(_location_fields(branch, warehouse))
    return row


async def read_physical_stock(
    db,
    item_id: str,
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
) -> dict:
    """Read physical_qty from physical_stock_balances only. Never inserts a balance."""
    master = await _require_master_item(db, item_id)
    branch = await _require_branch_any(db, branch_id) if branch_id else None
    warehouse = await _require_warehouse_any(db, warehouse_id) if warehouse_id else None
    if branch and warehouse and warehouse.get("branch_id") != branch.get("id"):
        raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")
    if warehouse and not branch:
        branch = await _require_branch_any(db, warehouse.get("branch_id"))

    query = {"master_item_id": master["id"]}
    if branch_id:
        query["branch_id"] = branch["id"]
    if warehouse_id:
        query["warehouse_id"] = warehouse["id"]
    docs = await db[BALANCE_COLLECTION].find(query, {"_id": 0}).sort(
        [("branch_id", 1), ("warehouse_id", 1)]
    ).to_list(5000)
    base_unit = await _base_unit(db, master)
    branches, warehouses = await _location_maps(
        db,
        {doc.get("branch_id") for doc in docs if doc.get("branch_id")},
        {doc.get("warehouse_id") for doc in docs if doc.get("warehouse_id")},
    )
    items = [
        _stock_row(
            master["id"],
            branches.get(doc.get("branch_id")),
            warehouses.get(doc.get("warehouse_id")),
            doc.get("physical_qty"),
            base_unit,
        )
        for doc in docs
    ]
    if warehouse and not items:
        items = [_stock_row(master["id"], branch, warehouse, 0, base_unit)]
    total = sum(row["physical_qty"] for row in items)
    return {
        "master_item_id": master["id"],
        "base_unit": base_unit,
        "total_physical_qty": total,
        "items": items,
    }


def _date_bound(value: str, *, end: bool) -> str:
    text = str(value).strip()
    if len(text) == 10 and text[4:5] == "-" and text[7:8] == "-":
        return text + ("T23:59:59.999999" if end else "T00:00:00")
    return text


_MOVEMENT_LABELS = {
    MOVEMENT_OPENING_BALANCE: "Saldo Awal",
    MOVEMENT_PURCHASE_RECEIPT: "Penerimaan Pembelian",
    MOVEMENT_PURCHASE_RETURN: "Retur Pembelian",
    MOVEMENT_SALES_DELIVERY: "Pengiriman",
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT: "Penyesuaian Stock Opname",
    MOVEMENT_MANUAL_ADJUSTMENT: "Penyesuaian",
    MOVEMENT_WAREHOUSE_TRANSFER_OUT: "Transfer Keluar",
    MOVEMENT_WAREHOUSE_TRANSFER_IN: "Transfer Masuk",
    MOVEMENT_REVERSAL: "Reversal",
    MOVEMENT_PRODUCTION_CONSUMPTION: "Pemakaian Produksi",
    MOVEMENT_PRODUCTION_OUTPUT: "Hasil Produksi",
}


def _mutation_row(doc: dict, branch: Optional[dict], warehouse: Optional[dict]) -> dict:
    movement_type = doc.get("movement_type")
    row = {
        "id": doc.get("id"),
        "occurred_at": doc.get("occurred_at"),
        "movement_type": movement_type,
        "movement_label": _MOVEMENT_LABELS.get(movement_type) or movement_type or "",
        "qty_delta": doc.get("qty_delta"),
        "balance_before": doc.get("balance_before"),
        "balance_after": doc.get("balance_after"),
        "reference_type": doc.get("reference_type"),
        "reference_id": doc.get("reference_id"),
        "reason": doc.get("reason"),
        "actor_id": doc.get("actor_id"),
        "metadata": doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {},
    }
    row.update(_location_fields(branch, warehouse))
    row["branch_id"] = doc.get("branch_id") or row["branch_id"]
    row["warehouse_id"] = doc.get("warehouse_id") or row["warehouse_id"]
    return row


async def list_physical_mutations(
    db,
    item_id: str,
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    movement_type: Optional[str] = None,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """Read physical_stock_ledger only. Newest occurred_at first. Never inserts."""
    master = await _require_master_item(db, item_id)
    branch = await _require_branch_any(db, branch_id) if branch_id else None
    warehouse = await _require_warehouse_any(db, warehouse_id) if warehouse_id else None
    if branch and warehouse and warehouse.get("branch_id") != branch.get("id"):
        raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")
    query: Dict[str, Any] = {"master_item_id": master["id"]}
    if branch:
        query["branch_id"] = branch["id"]
    if warehouse:
        query["warehouse_id"] = warehouse["id"]
    if movement_type:
        kind = str(movement_type).strip().upper()
        if kind not in MOVEMENT_TYPES:
            raise PhysicalStockValidationError("Jenis mutasi tidak dikenal")
        query["movement_type"] = kind
    occurred: Dict[str, str] = {}
    if from_date:
        occurred["$gte"] = _date_bound(from_date, end=False)
    if to_date:
        occurred["$lte"] = _date_bound(to_date, end=True)
    if occurred:
        query["occurred_at"] = occurred
    safe_page = page if isinstance(page, int) and page > 0 else 1
    safe_limit = limit if isinstance(limit, int) and limit > 0 else 50
    safe_limit = min(safe_limit, _MAX_READ_LIMIT)
    total = await db[LEDGER_COLLECTION].count_documents(query)
    docs = await db[LEDGER_COLLECTION].find(query, {"_id": 0}).sort(
        [("occurred_at", -1), ("created_at", -1), ("id", -1)]
    ).skip((safe_page - 1) * safe_limit).limit(safe_limit).to_list(safe_limit)
    branches, warehouses = await _location_maps(
        db,
        {doc.get("branch_id") for doc in docs if doc.get("branch_id")},
        {doc.get("warehouse_id") for doc in docs if doc.get("warehouse_id")},
    )
    items = [
        _mutation_row(doc, branches.get(doc.get("branch_id")), warehouses.get(doc.get("warehouse_id")))
        for doc in docs
    ]
    page_count = (total + safe_limit - 1) // safe_limit if total else 0
    return {
        "master_item_id": master["id"],
        "items": items,
        "total": total,
        "page": safe_page,
        "limit": safe_limit,
        "page_count": page_count,
    }


def _as_physical_qty(value: Any) -> int:
    if isinstance(value, bool):
        raise PhysicalStockValidationError("physical_qty harus bilangan bulat >= 0")
    if isinstance(value, int):
        qty = value
    elif isinstance(value, float) and value.is_integer() and abs(value) != float("inf"):
        qty = int(value)
    else:
        raise PhysicalStockValidationError("physical_qty harus bilangan bulat >= 0")
    if qty < 0:
        raise PhysicalStockValidationError("physical_qty harus bilangan bulat >= 0")
    return qty


def opening_reference_id(master_item_id: str, branch_id: str, warehouse_id: str) -> str:
    return f"opening:{master_item_id}:{branch_id}:{warehouse_id}"


def _same_adjustment(existing: dict, *, master_item_id: str, branch_id: str, warehouse_id: str, target: int, reason: str) -> bool:
    metadata = existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}
    try:
        stored_target = int(metadata.get("target_physical_qty"))
    except (TypeError, ValueError):
        return False
    return (
        existing.get("movement_type") == MOVEMENT_MANUAL_ADJUSTMENT
        and existing.get("master_item_id") == master_item_id
        and existing.get("branch_id") == branch_id
        and existing.get("warehouse_id") == warehouse_id
        and str(existing.get("reason") or "") == reason
        and stored_target == target
    )


def _adjustment_result(existing: dict, *, target: int, changed: bool) -> dict:
    return {
        "master_item_id": existing.get("master_item_id"),
        "branch_id": existing.get("branch_id"),
        "warehouse_id": existing.get("warehouse_id"),
        "previous_physical_qty": int(existing.get("balance_before") or 0),
        "target_physical_qty": target,
        "qty_delta": int(existing.get("qty_delta") or 0),
        "physical_qty": int(existing.get("balance_after") if existing.get("balance_after") is not None else target),
        "movement_id": existing.get("id"),
        "changed": changed,
    }


async def _adjust_to_target_once(
    db,
    *,
    master_item_id: str,
    branch_id: str,
    warehouse_id: str,
    target: int,
    reason: str,
    actor_id: str,
    idempotency_key: str,
) -> dict:
    ledger = db[LEDGER_COLLECTION]
    balances = db[BALANCE_COLLECTION]
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            existing = await _existing_movement(db, idempotency_key, session)
            if existing:
                if not _same_adjustment(
                    existing,
                    master_item_id=master_item_id,
                    branch_id=branch_id,
                    warehouse_id=warehouse_id,
                    target=target,
                    reason=reason,
                ):
                    raise PhysicalStockConflictError(
                        "Idempotency key sudah digunakan dengan payload yang berbeda"
                    )
                return _adjustment_result(existing, target=target, changed=True)
            await _guard_opname_freeze(
                db,
                session,
                warehouse_id=warehouse_id,
                movement_type=MOVEMENT_MANUAL_ADJUSTMENT,
            )
            await _validate_masters(
                db,
                master_item_id=master_item_id,
                branch_id=branch_id,
                warehouse_id=warehouse_id,
                session=session,
            )
            identity = {
                "master_item_id": master_item_id,
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
            }
            current_doc = await balances.find_one(
                identity,
                {"_id": 0, "physical_qty": 1, "baseline_status": 1},
                session=session,
            )
            current = int((current_doc or {}).get("physical_qty") or 0)
            delta = target - current
            prior_baseline = str((current_doc or {}).get("baseline_status") or "").strip()
            # Physical count target must be >= 0; book before may already be negative.
            if target < 0:
                raise PhysicalStockValidationError("Hasil hitung fisik harus >= 0")
            created_at = now_iso()
            # Zero qty delta: still reconcile an unreconciled baseline to the counted
            # target (including count=0). Do not invent a qty change; write an audit
            # MANUAL_ADJUSTMENT with qty_delta=0 so the count event is traceable.
            if delta == 0:
                if current_doc is None or prior_baseline != BASELINE_UNRECONCILED:
                    return {
                        "master_item_id": master_item_id,
                        "branch_id": branch_id,
                        "warehouse_id": warehouse_id,
                        "previous_physical_qty": current,
                        "target_physical_qty": target,
                        "qty_delta": 0,
                        "physical_qty": current,
                        "movement_id": None,
                        "changed": False,
                    }
                movement_id = str(uuid.uuid4())
                updated = await balances.find_one_and_update(
                    {
                        **identity,
                        "physical_qty": current,
                        "baseline_status": BASELINE_UNRECONCILED,
                    },
                    {
                        "$set": {
                            "baseline_status": BASELINE_RECONCILED,
                            "last_movement_id": movement_id,
                            "updated_at": created_at,
                        },
                        "$inc": {"version": 1},
                    },
                    upsert=False,
                    return_document=ReturnDocument.BEFORE,
                    session=session,
                )
                if not updated:
                    raise PhysicalStockRetryError("Saldo fisik berubah sebelum rekonsiliasi diterapkan")
                if _fail_after_balance:
                    raise PhysicalStockAbortError("forced transaction abort")
                doc = {
                    "id": movement_id,
                    "master_item_id": master_item_id,
                    "branch_id": branch_id,
                    "warehouse_id": warehouse_id,
                    "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
                    "qty_delta": 0,
                    "balance_before": current,
                    "balance_after": target,
                    "reference_type": MOVEMENT_MANUAL_ADJUSTMENT,
                    "reference_id": idempotency_key,
                    "reason": reason,
                    "actor_id": actor_id,
                    "occurred_at": created_at,
                    "created_at": created_at,
                    "idempotency_key": idempotency_key,
                    "metadata": {
                        "target_physical_qty": target,
                        "baseline_status": BASELINE_RECONCILED,
                        "reconcile_only": True,
                    },
                }
                await ledger.insert_one(doc, session=session)
                return _adjustment_result(doc, target=target, changed=True)
            movement_id = str(uuid.uuid4())
            update = {
                "$inc": {"physical_qty": delta, "version": 1},
                "$set": {
                    "last_movement_id": movement_id,
                    "updated_at": created_at,
                    "baseline_status": BASELINE_RECONCILED,
                },
            }
            if current_doc is None:
                update["$setOnInsert"] = {
                    "id": str(uuid.uuid4()),
                    "created_at": created_at,
                }
                before_doc = await balances.find_one_and_update(
                    identity,
                    update,
                    upsert=True,
                    return_document=ReturnDocument.BEFORE,
                    session=session,
                )
                balance_before = int((before_doc or {}).get("physical_qty") or 0)
            else:
                before_doc = await balances.find_one_and_update(
                    {**identity, "physical_qty": current},
                    update,
                    upsert=False,
                    return_document=ReturnDocument.BEFORE,
                    session=session,
                )
                if not before_doc:
                    raise PhysicalStockRetryError("Saldo fisik berubah sebelum delta diterapkan")
                balance_before = int(before_doc.get("physical_qty") or 0)
            if balance_before + delta != target:
                raise PhysicalStockRetryError("Saldo fisik berubah sebelum delta diterapkan")
            if _fail_after_balance:
                raise PhysicalStockAbortError("forced transaction abort")
            doc = {
                "id": movement_id,
                "master_item_id": master_item_id,
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
                "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
                "qty_delta": delta,
                "balance_before": balance_before,
                "balance_after": target,
                "reference_type": MOVEMENT_MANUAL_ADJUSTMENT,
                "reference_id": idempotency_key,
                "reason": reason,
                "actor_id": actor_id,
                "occurred_at": created_at,
                "created_at": created_at,
                "idempotency_key": idempotency_key,
                "metadata": {
                    "target_physical_qty": target,
                    "baseline_status": BASELINE_RECONCILED,
                },
            }
            await ledger.insert_one(doc, session=session)
            return _adjustment_result(doc, target=target, changed=True)


async def adjust_physical_stock_to_target(
    db,
    item_id: str,
    raw: dict,
    *,
    actor_id: str,
) -> dict:
    """Set one warehouse balance to a target qty by posting the delta in one transaction.

    The current balance is read again on every attempt, then the delta is
    recalculated against the target. A zero delta writes no qty change; if the
    baseline is still unreconciled it is marked reconciled with an audit row.
    """
    await require_transaction_support(_client(db))
    payload = raw or {}
    master_item_id = _as_text(item_id, "master_item_id")
    branch_id = _as_text(payload.get("branch_id"), "branch_id")
    warehouse_id = _as_text(payload.get("warehouse_id"), "warehouse_id")
    target = _as_physical_qty(payload.get("target_physical_qty"))
    reason = _as_text(payload.get("reason"), "reason")
    actor = _as_text(actor_id, "actor_id")
    idempotency_key = _as_text(payload.get("idempotency_key"), "idempotency_key")
    last_error: Optional[BaseException] = None
    for _attempt in range(_MAX_ATTEMPTS):
        try:
            return await _adjust_to_target_once(
                db,
                master_item_id=master_item_id,
                branch_id=branch_id,
                warehouse_id=warehouse_id,
                target=target,
                reason=reason,
                actor_id=actor,
                idempotency_key=idempotency_key,
            )
        except PhysicalStockRetryError as exc:
            last_error = exc
            continue
        except (PhysicalStockValidationError, PhysicalStockConflictError, InsufficientPhysicalStockError, PhysicalStockAbortError):
            raise
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    existing = await db[LEDGER_COLLECTION].find_one(
        {"idempotency_key": idempotency_key},
        {"_id": 0},
    )
    if existing:
        if not _same_adjustment(
            existing,
            master_item_id=master_item_id,
            branch_id=branch_id,
            warehouse_id=warehouse_id,
            target=target,
            reason=reason,
        ):
            raise PhysicalStockConflictError(
                "Idempotency key sudah digunakan dengan payload yang berbeda"
            )
        return _adjustment_result(existing, target=target, changed=True)
    raise PhysicalStockUnavailableError("Transaksi stok fisik gagal setelah percobaan ulang") from last_error


async def post_opening_balance(db, item_id: str, raw: dict, *, actor_id: str) -> dict:
    """Create the one-time OPENING_BALANCE movement through the R2 transaction engine."""
    payload = raw or {}
    branch_id = _as_text(payload.get("branch_id"), "branch_id")
    warehouse_id = _as_text(payload.get("warehouse_id"), "warehouse_id")
    physical_qty = _as_physical_qty(payload.get("physical_qty"))
    reason = str(payload.get("reason") or "").strip() or "Saldo awal"
    movement = await post_movement(
        db,
        {
            "master_item_id": _as_text(item_id, "master_item_id"),
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_OPENING_BALANCE,
            "qty_delta": physical_qty,
            "reference_type": MOVEMENT_OPENING_BALANCE,
            "reference_id": opening_reference_id(item_id, branch_id, warehouse_id),
            "reason": reason,
            "idempotency_key": _as_text(payload.get("idempotency_key"), "idempotency_key"),
        },
        actor_id=actor_id,
    )
    return {
        "established": True,
        "movement": movement,
        "physical_qty": movement.get("balance_after"),
    }


async def read_opening_balance(
    db,
    item_id: str,
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
) -> dict:
    """Read whether OPENING_BALANCE exists for one stock identity. Never writes."""
    if not branch_id or not warehouse_id:
        raise PhysicalStockValidationError("branch_id dan warehouse_id wajib diisi")
    master = await _require_master_item(db, item_id)
    branch = await _require_branch_any(db, branch_id)
    warehouse = await _require_warehouse_any(db, warehouse_id)
    if warehouse.get("branch_id") != branch.get("id"):
        raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")
    doc = await db[LEDGER_COLLECTION].find_one(
        {
            "master_item_id": master["id"],
            "branch_id": branch["id"],
            "warehouse_id": warehouse["id"],
            "movement_type": MOVEMENT_OPENING_BALANCE,
        },
        {"_id": 0},
    )
    status = {
        "master_item_id": master["id"],
        "branch_id": branch["id"],
        "warehouse_id": warehouse["id"],
        "established": False,
        "opening_movement_id": None,
        "opening_qty": None,
        "occurred_at": None,
        "actor_id": None,
    }
    if not doc:
        return status
    status.update({
        "established": True,
        "opening_movement_id": doc.get("id"),
        "opening_qty": doc.get("qty_delta"),
        "occurred_at": doc.get("occurred_at"),
        "actor_id": doc.get("actor_id") or "",
    })
    return status


def _order_cancelled(order: dict) -> bool:
    if order.get("cancelled") or order.get("is_cancelled"):
        return True
    status = str(order.get("status") or order.get("status_label") or "").strip().lower()
    return status in ("cancelled", "dibatalkan", "void")


def _qty_num(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number != number:
        return 0.0
    if number == int(number):
        return float(int(number))
    return number


def _is_operational_branch(branch: dict) -> bool:
    code = str(branch.get("code") or "").strip().upper()
    name = str(branch.get("name") or "").strip().lower()
    return code == "YGY" or "yogyakarta" in name


def resolve_operational_branch(branches: Iterable[dict]) -> Optional[dict]:
    """Default operational branch: Yogyakarta (YGY), else sole active branch.

    Never aggregates multiple branches. When YGY is absent and exactly one
    active branch exists (typical isolated UAT), that branch is the default.
    """
    all_rows = [row for row in branches if row.get("id")]
    rows = [row for row in all_rows if _is_operational_branch(row)]
    coded = [row for row in rows if str(row.get("code") or "").strip().upper() == "YGY"]
    if coded:
        return coded[0]
    if rows:
        return rows[0]
    active = [row for row in all_rows if row.get("active") is not False]
    if len(active) == 1:
        return active[0]
    return None


def _line_master_id(line: dict, accurate_owners: Dict[str, str]) -> str:
    master_id = str(line.get("master_item_id") or "").strip()
    if master_id:
        return master_id
    accurate_id = str(line.get("accurate_item_id") or line.get("accurate_id") or "").strip()
    if not accurate_id:
        return ""
    return accurate_owners.get(accurate_id) or ""


def operational_availability(
    *,
    physical_qty: Any,
    ordered_qty: Any,
    completed_qty: Any,
    r2_delivered_qty: Any = 0,
) -> dict:
    """Qty Tersedia = Stock Fisik (buku) - Qty Pesanan - Qty Belum Terkirim.

    Book physical_qty is not clamped to zero — negative book and negative
    qty_tersedia are informational and must not block orders/production.
    Operator completion only moves Qty Pesanan into Qty Belum Terkirim.
    Pass r2_delivered_qty only for quantity already posted as R2 SALES_DELIVERY.
    """
    physical = _qty_num(physical_qty)
    ordered = max(_qty_num(ordered_qty), 0.0)
    completed = max(_qty_num(completed_qty), 0.0)
    delivered = max(_qty_num(r2_delivered_qty), 0.0)
    qty_pesanan = max(ordered - completed, 0.0)
    qty_belum_terkirim = max(min(completed, ordered) - min(delivered, min(completed, ordered)), 0.0)
    qty_tersedia = physical - qty_pesanan - qty_belum_terkirim
    return {
        "physical_qty": physical,
        "qty_pesanan": qty_pesanan,
        "qty_belum_terkirim": qty_belum_terkirim,
        "qty_tersedia": qty_tersedia,
    }


def _line_item_cancelled(line: dict) -> bool:
    return bool(line.get("cancelled") or line.get("is_cancelled"))


def aggregate_operational_orders(
    orders: Iterable[dict],
    accurate_owners: Optional[Dict[str, str]] = None,
    *,
    exclude_master_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Dict[str, float]]:
    """Sum active ordered qty and ready qty per master_item_id.

    Print lines: ready = operator qty_completed (unassigned splits only attach to
    the sole print line when exactly one print process candidate exists —
    including SERVICE process Add Ons that are excluded from demand).
    Non-print lines: ready = ordered qty (siap kirim) without writing completed
    or packed fields on the order. Cancelled orders contribute nothing.
    Masters in exclude_master_ids (e.g. item_type=SERVICE process Add On) never
    contribute physical stock demand — work qty stays on production tracking.

    Explicit parent_line_id / parent_line_idx links process Add Ons to a parent
    on THIS order only (never global by master item). FULLY_COMPLETED when
    parent + linked processes have operator confirmation covering ordered qty
    → physical siap = parent qty. Partial stays fail-closed (no READY via min).
    Unlinked SERVICE/orphan print processes mark UNVERIFIED for affected lines.
    Never invent links from item names.
    """
    from quantity_rules import is_print_line, operational_ready_qty_for_line
    from services.order_line_addon import (
        STATUS_FULLY_COMPLETED,
        evaluate_process_group,
        process_children_for_parent,
    )

    owners = accurate_owners or {}
    excluded = {str(mid).strip() for mid in (exclude_master_ids or []) if str(mid).strip()}
    totals: Dict[str, Dict[str, Any]] = {}

    def add(master_id: str, ordered: float, completed: float, *, unproven: bool = False) -> None:
        if not master_id or master_id in excluded:
            return
        slot = totals.setdefault(
            master_id,
            {"ordered_qty": 0.0, "completed_qty": 0.0, "completion_unproven": False},
        )
        slot["ordered_qty"] = _qty_num(slot["ordered_qty"]) + _qty_num(ordered)
        slot["completed_qty"] = _qty_num(slot["completed_qty"]) + _qty_num(completed)
        if unproven:
            slot["completion_unproven"] = True

    for order in orders:
        if _order_cancelled(order):
            continue
        lines = list(order.get("line_items") or [])
        master_ids = [_line_master_id(line, owners) for line in lines]
        active_idxs = [
            idx for idx, line in enumerate(lines)
            if isinstance(line, dict) and not _line_item_cancelled(line)
        ]
        # All active print process candidates (SERVICE + orphan included).
        print_candidates = [idx for idx in active_idxs if is_print_line(lines[idx])]
        has_orphan_print = any(not master_ids[idx] for idx in print_candidates)

        # Track completions for physical AND process (SERVICE) print lines.
        process_completed_by_idx: Dict[int, float] = {}
        completed_by_idx: Dict[int, float] = {}
        unassigned = []
        for split in order.get("operator_splits") or []:
            raw_idx = split.get("line_idx")
            if raw_idx is None or str(raw_idx).strip() == "":
                unassigned.append(split)
                continue
            try:
                idx = int(raw_idx)
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(lines) or not master_ids[idx]:
                continue
            if _line_item_cancelled(lines[idx]):
                continue
            if not is_print_line(lines[idx]):
                continue
            qty = _qty_num(split.get("qty_completed"))
            process_completed_by_idx[idx] = _qty_num(process_completed_by_idx.get(idx, 0)) + qty
            if master_ids[idx] not in excluded:
                completed_by_idx[idx] = _qty_num(completed_by_idx.get(idx, 0)) + qty

        unassigned_ambiguous = False
        if unassigned:
            # Attribute only when exactly one print process candidate exists overall.
            if len(print_candidates) == 1 and master_ids[print_candidates[0]] and (
                master_ids[print_candidates[0]] not in excluded
            ):
                idx = print_candidates[0]
                extra = sum(_qty_num(split.get("qty_completed")) for split in unassigned)
                process_completed_by_idx[idx] = _qty_num(process_completed_by_idx.get(idx, 0)) + extra
                completed_by_idx[idx] = _qty_num(completed_by_idx.get(idx, 0)) + extra
            else:
                unassigned_ambiguous = True

        # Detect unlinked process Add Ons once per order (fail-closed).
        order_unlinked_process = False
        children_by_parent: Dict[int, List[int]] = {}
        for idx in active_idxs:
            mid = master_ids[idx]
            if not mid or mid in excluded:
                continue
            children, unlinked = process_children_for_parent(
                idx,
                lines,
                process_master_ids=excluded,
                master_ids=master_ids,
                is_print_line=is_print_line,
            )
            children_by_parent[idx] = children
            if unlinked:
                order_unlinked_process = True

        process_gate_unproven = (
            order_unlinked_process or has_orphan_print or unassigned_ambiguous
        )

        for idx in active_idxs:
            mid = master_ids[idx]
            if not mid or mid in excluded:
                continue
            line = lines[idx]
            ordered = _qty_num(line.get("quantity"))
            if not is_print_line(line):
                ready = operational_ready_qty_for_line(order, idx)
                add(mid, ordered, ready, unproven=False)
                continue

            parent_completed = min(_qty_num(completed_by_idx.get(idx, 0)), ordered)
            children = children_by_parent.get(idx) or []
            if children:
                group = evaluate_process_group(
                    order,
                    idx,
                    process_master_ids=excluded,
                    master_ids=master_ids,
                    is_print_line=is_print_line,
                    completed_by_idx=process_completed_by_idx,
                )
                if group.status == STATUS_FULLY_COMPLETED and not process_gate_unproven:
                    # Physical siap = parent qty only (not sum of SERVICE process).
                    add(mid, ordered, group.physical_ready_qty, unproven=False)
                elif group.status == STATUS_FULLY_COMPLETED and process_gate_unproven:
                    # Linked group looks done but PO has orphan/ambiguous process.
                    add(mid, ordered, group.physical_ready_qty, unproven=True)
                else:
                    # Fail-closed partial / incomplete — no READY via min().
                    add(mid, ordered, 0.0, unproven=False)
            elif process_gate_unproven:
                # Unlinked/orphan/ambiguous process work on this PO — fail-closed.
                add(mid, ordered, parent_completed, unproven=True)
            else:
                add(mid, ordered, parent_completed, unproven=False)
    return totals


def _catalog_page(page: int, limit: int) -> Tuple[int, int]:
    safe_page = page if isinstance(page, int) and page > 0 else 1
    safe_limit = limit if isinstance(limit, int) and limit > 0 else 50
    return safe_page, min(safe_limit, _MAX_READ_LIMIT)


async def _unique_accurate_owners(db) -> Dict[str, str]:
    rows = await db[MASTER_ITEMS_COLLECTION].find(
        {},
        {"_id": 0, "id": 1, "accurate_item_id": 1},
    ).to_list(5000)
    counts: Dict[str, int] = {}
    owner: Dict[str, str] = {}
    for row in rows:
        accurate_id = str(row.get("accurate_item_id") or "").strip()
        master_id = str(row.get("id") or "").strip()
        if not accurate_id or not master_id:
            continue
        counts[accurate_id] = counts.get(accurate_id, 0) + 1
        owner[accurate_id] = master_id
    return {accurate_id: master_id for accurate_id, master_id in owner.items() if counts[accurate_id] == 1}


def _order_branch_id(order: Optional[dict]) -> str:
    """Proven branch on an order. Empty when legacy attribution is missing."""
    if not isinstance(order, dict):
        return ""
    return str(order.get("branch_id") or order.get("cabang_id") or "").strip()


def _orders_for_branch(
    orders: Iterable[dict],
    branch_id: Optional[str],
    *,
    legacy_settings: Optional[dict] = None,
) -> List[dict]:
    """Keep only orders attributed to this branch.

    Default: exact branch_id/cabang_id match (fail-closed for missing ids).
    When Mode B legacy demand settings are present, attribution (historical
    Accurate/null cutoff rules and optional Accurate Live branch map) runs via
    services.mode_b_legacy_demand — never rewrites documents.
    """
    wanted = str(branch_id or "").strip()
    if not wanted:
        return []
    if legacy_settings is not None:
        from services.mode_b_legacy_demand import filter_orders_for_branch

        return filter_orders_for_branch(orders, wanted, legacy_settings)
    return [
        order for order in orders
        if _order_branch_id(order) == wanted
    ]


async def _r2_confirmed_delivery_qty(
    db,
    master_ids: List[str],
    *,
    branch_id: Optional[str] = None,
) -> Dict[str, float]:
    """Delivered qty that already left R2 physical stock (net of reversals).

    The SALES_DELIVERY ledger movement is the proof. A legacy sales_deliveries
    row, surat jalan, or loading record does not reduce Qty Belum Terkirim.
    Reversed SALES_DELIVERY rows are excluded.

    When branch_id is set, only ledger rows for that branch count — never mix
    Yogyakarta deliveries into Semarang Belum Terkirim (or the reverse).
    """
    if not master_ids:
        return {}
    query: Dict[str, Any] = {
        "movement_type": MOVEMENT_SALES_DELIVERY,
        "master_item_id": {"$in": master_ids},
    }
    wanted_branch = str(branch_id or "").strip()
    if wanted_branch:
        query["branch_id"] = wanted_branch
    movements = await db[LEDGER_COLLECTION].find(
        query,
        {"_id": 0, "id": 1, "master_item_id": 1, "qty_delta": 1, "branch_id": 1},
    ).to_list(20000)
    movement_ids = [str(row.get("id") or "") for row in movements if row.get("id")]
    reversed_ids = set()
    if movement_ids:
        reversals = await db[LEDGER_COLLECTION].find(
            {
                "movement_type": MOVEMENT_REVERSAL,
                "reference_id": {"$in": movement_ids},
            },
            {"_id": 0, "reference_id": 1},
        ).to_list(20000)
        reversed_ids = {str(row.get("reference_id") or "") for row in reversals}
    confirmed: Dict[str, float] = {}
    for row in movements:
        if str(row.get("id") or "") in reversed_ids:
            continue
        delta = _qty_num(row.get("qty_delta"))
        if delta >= 0:
            continue
        master_id = str(row.get("master_item_id") or "")
        confirmed[master_id] = _qty_num(confirmed.get(master_id, 0)) + abs(delta)
    return confirmed


async def operational_physical_qty_by_item(
    db,
    master_ids: Optional[List[str]] = None,
    *,
    branch_id: Optional[str] = None,
) -> Dict[str, float]:
    """Sum R2 physical_qty on operational warehouses for ONE branch. Read only.

    Default branch is Yogyakarta (resolve_operational_branch) when branch_id omitted.
    Never aggregates multiple branches. Missing balances are omitted from the map
    (callers must treat omission as UNAVAILABLE, not proven zero).
    """
    all_branches = await db[BRANCHES_COLLECTION].find(
        {},
        {"_id": 0, "id": 1, "code": 1, "name": 1},
    ).to_list(500)
    requested = str(branch_id or "").strip()
    if requested:
        operational = next((row for row in all_branches if str(row.get("id") or "") == requested), None)
    else:
        operational = resolve_operational_branch(all_branches)
    if not operational:
        return {}
    warehouses = await db[WAREHOUSES_COLLECTION].find(
        {"branch_id": operational["id"]},
        {"_id": 0, "id": 1, "is_operational": 1},
    ).to_list(500)
    warehouse_ids = [
        row.get("id")
        for row in warehouses
        if row.get("id") and warehouse_is_operational(row)
    ]
    if not warehouse_ids:
        return {}
    query: Dict[str, Any] = {"warehouse_id": {"$in": warehouse_ids}}
    if master_ids is not None:
        ids = [str(mid).strip() for mid in master_ids if str(mid or "").strip()]
        if not ids:
            return {}
        query["master_item_id"] = {"$in": ids}
    docs = await db[BALANCE_COLLECTION].find(
        query,
        {"_id": 0, "master_item_id": 1, "physical_qty": 1},
    ).to_list(20000)
    totals: Dict[str, float] = {}
    for doc in docs:
        mid = str(doc.get("master_item_id") or "").strip()
        if not mid:
            continue
        totals[mid] = _qty_num(totals.get(mid, 0)) + _qty_num(doc.get("physical_qty"))
    return totals


def _warehouse_engine_catalog_envelope(
    *,
    items: List[dict],
    total: int,
    page: int,
    limit: int,
    page_count: int,
    master_item_count: int,
    warehouse_count: int,
    catalog_mode: str,
    r2_on: bool,
    standalone_on: bool,
    selected_warehouse: Optional[dict],
) -> dict:
    """Shared Stock Saat Ini envelope for warehouse-engine reads (R2 or standalone).

    Empty catalogs (no BARANG masters / no search hits) MUST still carry mode
    flags so the UI does not fall back to compatibility chrome.
    """
    from services.warehouse_stock_standalone import MODE_WAREHOUSE_STANDALONE

    selected = selected_warehouse or None
    if selected_warehouse:
        note = (
            "Saldo buku gudang terpilih (boleh negatif)."
            if catalog_mode == MODE_WAREHOUSE_STANDALONE
            else "Saldo buku R2 gudang terpilih (boleh negatif)."
        )
    else:
        note = (
            "Saldo buku agregat cabang; rincian per gudang ada di warehouse_balances."
            if catalog_mode == MODE_WAREHOUSE_STANDALONE
            else "Saldo buku R2 agregat cabang; rincian per gudang ada di warehouse_balances."
        )
    return {
        "items": items,
        "total": total,
        "page": page,
        "limit": limit,
        "page_count": page_count,
        "master_item_count": master_item_count,
        "warehouse_count": warehouse_count,
        "stock_read_mode": catalog_mode,
        "r2_authority_active": bool(r2_on),
        "warehouse_stock_standalone": bool(standalone_on),
        "physical_qty_scope": "WAREHOUSE" if selected else "BRANCH",
        "branch_filter_affects_physical_qty": True,
        "warehouse_filter_affects_physical_qty": bool(selected),
        "effective_warehouse_id": (
            str(selected.get("id") or "") if selected else None
        ),
        "effective_warehouse_code": (
            (selected.get("code") or None) if selected else None
        ),
        "demand_scope": "BRANCH",
        "physical_qty_note": note,
    }


async def list_physical_stock_catalog(
    db,
    *,
    q: Optional[str] = None,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """Operational Stock Saat Ini.

    When r2_authority_active is FALSE and warehouse_stock_standalone is OFF:
    read-only compatibility adapter (legacy company physical + orders).

    When r2_authority_active is TRUE, OR warehouse_stock_standalone is ON
    (R2 still OFF): Stock fisik is the sum of physical_stock_balances on
    operational warehouses for ONE branch (default YGY). Never aggregates all
    branches. Optional warehouse_id further narrows within that branch.

    Standalone mode reuses the same balance documents as R2 reads but never
    activates R2 authority or multi-document transactions.
    """
    from services.r2_go_live import is_r2_authority_active
    from services.stock_compatibility_read import list_compatibility_stock_catalog
    from services.warehouse_stock_standalone import (
        MODE_WAREHOUSE_STANDALONE,
        is_warehouse_stock_standalone_active,
    )

    r2_on = await is_r2_authority_active(db)
    standalone_on = False if r2_on else await is_warehouse_stock_standalone_active(db)
    if not r2_on and not standalone_on:
        return await list_compatibility_stock_catalog(db, q=q, page=page, limit=limit)
    catalog_mode = "r2" if r2_on else MODE_WAREHOUSE_STANDALONE

    all_branches = await db[BRANCHES_COLLECTION].find({}, {"_id": 0, "id": 1, "code": 1, "name": 1}).to_list(500)
    requested = str(branch_id or "").strip()
    if requested:
        operational = next((row for row in all_branches if str(row.get("id") or "") == requested), None)
    else:
        operational = resolve_operational_branch(all_branches)
    operational_id = (operational or {}).get("id")
    warehouses = []
    if operational_id:
        warehouses = await db[WAREHOUSES_COLLECTION].find(
            {"branch_id": operational_id},
            {"_id": 0},
        ).sort([("code", 1), ("id", 1)]).to_list(500)
        warehouses = [row for row in warehouses if warehouse_is_operational(row)]
        requested_wh = str(warehouse_id or "").strip()
        if requested_wh:
            warehouses = [row for row in warehouses if str(row.get("id") or "") == requested_wh]
    item_query: Dict[str, Any] = {}
    text = str(q or "").strip()
    if text:
        pattern = re.escape(text)
        item_query["$or"] = [
            {"item_code": {"$regex": pattern, "$options": "i"}},
            {"item_name": {"$regex": pattern, "$options": "i"}},
            {"accurate_item_id": {"$regex": pattern, "$options": "i"}},
        ]
    masters = await db[MASTER_ITEMS_COLLECTION].find(item_query, {"_id": 0}).sort(
        [("item_name", 1), ("id", 1)]
    ).to_list(1000)
    from services.inventory_accounting import (
        ITEM_TYPE_SERVICE,
        is_stock_saat_ini_inventory_catalog_item,
        stock_saat_ini_condition_unavailable_fields,
    )

    type_rows = await db[MASTER_ITEMS_COLLECTION].find(
        {}, {"_id": 0, "id": 1, "item_type": 1},
    ).to_list(5000)
    # SERVICE masters stay available for delivery/process remap — not catalog rows.
    service_master_ids = {
        str(row.get("id") or "").strip()
        for row in type_rows
        if str(row.get("item_type") or "").strip().upper() == ITEM_TYPE_SERVICE
        and str(row.get("id") or "").strip()
    }
    type_by_id = {
        str(row.get("id") or "").strip(): row.get("item_type")
        for row in type_rows
        if str(row.get("id") or "").strip()
    }
    # Stock Saat Ini catalog: explicit INVENTORY only (exclude SERVICE,
    # NON_INVENTORY, null/unknown). Does not mutate master_items.
    masters = [
        row for row in masters
        if is_stock_saat_ini_inventory_catalog_item(
            type_by_id.get(str(row.get("id") or "").strip(), row.get("item_type"))
        )
    ]
    safe_page, safe_limit = _catalog_page(page, limit)
    selected_warehouse_id = str(warehouse_id or "").strip()
    selected_warehouse = next(
        (
            row for row in warehouses
            if str(row.get("id") or "") == selected_warehouse_id
        ),
        None,
    ) if selected_warehouse_id else None
    if not masters:
        # Keep mode metadata identical to a non-empty catalog so the FE does not
        # collapse warehouse chrome to compatibility when search/SERVICE yields 0.
        return _warehouse_engine_catalog_envelope(
            items=[],
            total=0,
            page=safe_page,
            limit=safe_limit,
            page_count=0,
            master_item_count=0,
            warehouse_count=len(warehouses),
            catalog_mode=catalog_mode,
            r2_on=r2_on,
            standalone_on=standalone_on,
            selected_warehouse=selected_warehouse,
        )
    units = await _catalog_units(db, masters)
    owners = await _unique_accurate_owners(db)
    from services.mode_b_legacy_demand import (
        get_mode_b_legacy_demand_settings,
        mongo_order_branch_clauses,
    )
    from services.stock_compatibility_read import (
        BELUM_STATUS_UNVERIFIED,
        aggregate_legacy_delivered_qty,
    )

    legacy_settings = await get_mode_b_legacy_demand_settings(db)
    # Demand is branch-scoped. Default excludes unattributed orders; optional
    # Mode B legacy policy may attribute historical Accurate/null ids to one
    # configured org branch without rewriting documents.
    order_query: Dict[str, Any] = {}
    if operational_id:
        order_query["$or"] = mongo_order_branch_clauses(operational_id, legacy_settings)
    else:
        # No resolvable branch → no inventable demand.
        order_query = {"_id": {"$exists": False}}
    orders = await db.orders.find(order_query, {"_id": 0}).to_list(20000)
    # Defense in depth: filter again in Python (cabang_id / stray docs / cutoff).
    orders = _orders_for_branch(
        orders, operational_id, legacy_settings=legacy_settings,
    )
    progress = aggregate_operational_orders(
        orders, owners, exclude_master_ids=service_master_ids,
    )
    master_ids = [master["id"] for master in masters if master.get("id")]
    # Qty Belum Terkirim delivery proof = Compatibility live rules (confirmed
    # shipment / qty_delivered fallback). Do NOT also add SALES_DELIVERY ledger
    # here — ledger is Stock Fisik authority only (avoids double-count once Mode B
    # confirmations also stamp order.shipments). R2 ledger path remains for R2.
    if catalog_mode == "r2":
        delivered = await _r2_confirmed_delivery_qty(
            db, master_ids, branch_id=operational_id,
        )
        delivery_provenance: Dict[str, str] = {
            mid: "r2_ledger" for mid in delivered
        }
    else:
        delivered, delivery_provenance = aggregate_legacy_delivered_qty(
            orders,
            [],
            owners,
            process_master_ids=service_master_ids,
        )
    warehouse_ids = [row.get("id") for row in warehouses if row.get("id")]
    balance_docs = []
    if warehouse_ids and master_ids:
        balance_docs = await db[BALANCE_COLLECTION].find(
            {
                "master_item_id": {"$in": master_ids},
                "warehouse_id": {"$in": warehouse_ids},
            },
            {"_id": 0},
        ).to_list(max(len(master_ids) * len(warehouse_ids), 1))
    balances: Dict[str, List[dict]] = {}
    for doc in balance_docs:
        balances.setdefault(doc.get("master_item_id"), []).append(doc)

    # Owner Excel SSI components (Saldo Awal / Pembelian / delivered) from R2 ledger.
    # Read-only aggregation — no dual stock mutation.
    from services.stock_saat_ini_excel import (
        aggregate_ledger_components,
        aggregate_proven_print_delivered_qty,
        apply_excel_fields_to_catalog_row,
        compute_excel_columns,
        resolve_qty_tercetak,
    )

    ledger_components: Dict[str, Dict[str, float]] = {}
    if master_ids and operational_id and catalog_mode == "r2":
        ledger_query: Dict[str, Any] = {
            "master_item_id": {"$in": master_ids},
            "branch_id": operational_id,
            "movement_type": {
                "$in": [
                    MOVEMENT_OPENING_BALANCE,
                    MOVEMENT_PURCHASE_RECEIPT,
                    MOVEMENT_SALES_DELIVERY,
                    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
                    MOVEMENT_MANUAL_ADJUSTMENT,
                    MOVEMENT_REVERSAL,
                ]
            },
        }
        selected_wh = str(warehouse_id or "").strip()
        if selected_wh:
            ledger_query["warehouse_id"] = selected_wh
        ledger_docs = await db[LEDGER_COLLECTION].find(ledger_query, {"_id": 0}).to_list(50000)
        ledger_components = aggregate_ledger_components(
            ledger_docs,
            master_item_ids=master_ids,
            branch_id=operational_id,
            warehouse_id=selected_wh or None,
        )

    # Proven printed SJ qty only (never estimate from total so_delivered).
    print_delivered, print_delivery_prov = aggregate_proven_print_delivered_qty(
        orders, owners, process_master_ids=service_master_ids,
    )

    rows = []
    for master in masters:
        docs = balances.get(master.get("id"), [])
        physical = sum(_qty_num(doc.get("physical_qty")) for doc in docs)
        updated = [doc.get("updated_at") for doc in docs if doc.get("updated_at")]
        demand = progress.get(master.get("id")) or {
            "ordered_qty": 0.0,
            "completed_qty": 0.0,
            "completion_unproven": False,
        }
        mid = master.get("id")
        # Compatibility parity: delivered from proven shipments / qty_delivered only
        # (standalone Mode B). R2 still uses ledger map above.
        effective_delivered = _qty_num(delivered.get(mid, 0))
        completion_unproven = bool(demand.get("completion_unproven"))
        delivery_unproven = str(delivery_provenance.get(str(mid) or mid, "") or "") == "unproven"
        completed_for_buckets = demand["completed_qty"]
        buckets = operational_availability(
            physical_qty=physical,
            ordered_qty=demand["ordered_qty"],
            completed_qty=completed_for_buckets,
            r2_delivered_qty=effective_delivered,
        )
        # Fail-closed siap kirim when process Add On completion cannot be proven
        # OR Compatibility delivery attribution is unproven for this master.
        belum_unverified = bool(
            (completion_unproven and _qty_num(completed_for_buckets) > 0)
            or (delivery_unproven and _qty_num(completed_for_buckets) > 0)
        )
        if belum_unverified:
            buckets = dict(buckets)
            buckets["qty_belum_terkirim"] = None  # type: ignore[assignment]
            # Qty Tersedia informational only excludes pesanan (not fabricated belum).
            buckets["qty_tersedia"] = (
                _qty_num(buckets["physical_qty"]) - _qty_num(buckets["qty_pesanan"])
            )
        docs_by_wh = {
            str(doc.get("warehouse_id") or ""): doc
            for doc in docs
            if doc.get("warehouse_id")
        }
        # Per-warehouse book balances so "Semua Gudang" can show shortages
        # independently of the branch aggregate. Missing balance ≠ verified zero.
        warehouse_balances = []
        scope_baselines: List[str] = []
        candidate_movement_ids: List[str] = []
        for wh in warehouses:
            wid = str(wh.get("id") or "")
            if not wid:
                continue
            doc = docs_by_wh.get(wid)
            if doc is None:
                wh_qty = None
                wh_baseline = BASELINE_UNRECONCILED
                wh_label = BASELINE_UNRECONCILED_LABEL
                wh_movement_id = None
                wh_updated_at = None
            else:
                wh_qty = _qty_num(doc.get("physical_qty"))
                wh_baseline = (
                    str(doc.get("baseline_status") or BASELINE_RECONCILED).strip()
                    or BASELINE_RECONCILED
                )
                wh_label = (
                    BASELINE_UNRECONCILED_LABEL
                    if wh_baseline == BASELINE_UNRECONCILED
                    else None
                )
                wh_movement_id = str(doc.get("last_movement_id") or "").strip() or None
                wh_updated_at = doc.get("updated_at")
                if wh_movement_id:
                    candidate_movement_ids.append(wh_movement_id)
            scope_baselines.append(wh_baseline)
            warehouse_balances.append({
                "warehouse_id": wid,
                "warehouse_code": wh.get("code") or "",
                "warehouse_name": wh.get("name") or "",
                "physical_qty": wh_qty,
                "baseline_status": wh_baseline,
                "baseline_label": wh_label,
                "saldo_buku_negatif": bool(wh_qty is not None and wh_qty < 0),
                "balance_present": doc is not None,
                "physical_qty_is_book": True,
                "updated_at": wh_updated_at,
                "updated_by_name": None,
                "_last_movement_id": wh_movement_id,
            })
        # Branch summary valid only when EVERY operational warehouse in scope has
        # a RECONCILED balance. Selected-warehouse view uses that gudang alone
        # (warehouses list is already filtered to the selection).
        if not scope_baselines or any(
            status != BASELINE_RECONCILED for status in scope_baselines
        ):
            baseline_status = BASELINE_UNRECONCILED
        else:
            baseline_status = BASELINE_RECONCILED
        # Reconciled warehouse book + proven Belum (Compatibility rules).
        qty_tersedia_valid = (
            baseline_status == BASELINE_RECONCILED and not belum_unverified
        )
        if belum_unverified:
            if delivery_unproven:
                tersedia_note = (
                    "Qty Tersedia belum valid — Qty Belum Terkirim belum terverifikasi "
                    "(bukti pengiriman ambigu / BELUM_TERVERIFIKASI)."
                )
            else:
                tersedia_note = (
                    "Qty Tersedia belum valid — Qty Belum Terkirim belum terverifikasi "
                    "(proses Add On / completion tidak dapat dibuktikan)."
                )
        elif baseline_status != BASELINE_RECONCILED:
            tersedia_note = "Qty Tersedia belum valid — saldo fisik belum direkonsiliasi"
        else:
            tersedia_note = None
        row = {
            "master_item_id": master.get("id"),
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or "",
            "accurate_item_id": str(master.get("accurate_item_id") or ""),
            "base_unit": units.get(master.get("id")) or "PCS",
            "physical_qty": buckets["physical_qty"],
            "qty_pesanan": buckets["qty_pesanan"],
            "qty_belum_terkirim": buckets["qty_belum_terkirim"],
            # Informational book math only — not definitive until baseline reconciled
            # and Belum Terkirim is verified. Legacy field retained for API clients.
            "qty_tersedia": buckets["qty_tersedia"],
            "qty_tersedia_valid": qty_tersedia_valid,
            "qty_tersedia_note": tersedia_note,
            # Placeholder condition fields — overwritten by Owner Excel formula below.
            **stock_saat_ini_condition_unavailable_fields(),
            "delivery_provenance": delivery_provenance.get(str(mid) or mid),
            # Provisional from balance stamps; replaced by the same final movement
            # used for updated_by_name during page enrich (never master_items.updated_by).
            "updated_at": max(updated) if updated else None,
            "updated_by_name": None,
            "_candidate_movement_ids": candidate_movement_ids,
            "belum_terkirim_status": (
                BELUM_STATUS_UNVERIFIED if belum_unverified else "READY"
            ),
            "stock_read_mode": catalog_mode,
            "baseline_status": baseline_status,
            "baseline_label": (
                BASELINE_UNRECONCILED_LABEL if baseline_status == BASELINE_UNRECONCILED else None
            ),
            "saldo_buku_negatif": buckets["physical_qty"] < 0,
            "physical_qty_is_book": True,
            "warehouse_balances": warehouse_balances,
            # Demand/availability is branch-level — never fabricate per-warehouse
            # allocation of orders across gudang.
            "demand_scope": "BRANCH",
            "availability_scope": "BRANCH",
        }
        # Owner Excel SSI (PCS): Saldo Awal, Pembelian, Stock, Stock Dapat Dijual,
        # Qty Tercetak / Polos / Tercetak Belum Kirim / Cetak Sudah Dikirim.
        # Uses existing order + delivery + ledger events — no second mutation path.
        comp = ledger_components.get(str(mid) or mid) or {}
        balance_present_any = any(
            bool(wb.get("balance_present")) for wb in warehouse_balances
        )
        tercetak_slot = resolve_qty_tercetak(demand)
        mid_key = str(mid) or mid
        print_prov = str(print_delivery_prov.get(mid_key, "") or "")
        delivery_prov = str(delivery_provenance.get(mid_key, "") or "")
        # Print delivery is proven only from confirmed print-line SJ attribution.
        # Ambiguous total delivery or print attribution → null (not estimate).
        print_unproven = print_prov == "unproven" or delivery_prov == "unproven"
        print_proven = (
            print_prov == "confirmed_print_shipment" and not print_unproven
        )
        cetak_proven_qty = print_delivered.get(mid_key, 0.0) if print_proven else None
        # Verified zero: no unproven flag and no confirmed print shipments.
        if not print_unproven and not print_proven:
            print_proven = True
            cetak_proven_qty = 0.0
        excel = compute_excel_columns(
            saldo_awal=comp.get("saldo_awal", 0),
            pembelian=comp.get("pembelian", 0),
            adjustment_net=comp.get("adjustment_net", 0),
            delivered_total=effective_delivered,
            po_ordered=demand["ordered_qty"],
            so_delivered=effective_delivered,
            qty_tercetak=tercetak_slot["qty"],
            qty_tercetak_status=tercetak_slot["status"],
            cetak_delivered_proven=cetak_proven_qty,
            print_delivery_proven=print_proven and not print_unproven,
            stock_book=buckets["physical_qty"] if balance_present_any else None,
        )
        apply_excel_fields_to_catalog_row(
            row,
            excel,
            balance_present=balance_present_any or bool(comp),
        )
        row["print_delivery_provenance"] = print_prov or (
            "verified_zero" if print_proven else ""
        )
        if selected_warehouse:
            row["warehouse_id"] = str(selected_warehouse.get("id") or "")
            row["warehouse_code"] = selected_warehouse.get("code") or ""
            row["warehouse_name"] = selected_warehouse.get("name") or ""
            row["physical_qty_scope"] = "WAREHOUSE"
            row["availability_scope"] = "BRANCH_DEMAND_VS_WAREHOUSE_BOOK"
            row["demand_scope_note"] = (
                "Qty Pesanan / Belum Terkirim adalah lingkup cabang; "
                "bukan alokasi per gudang."
            )
            scope_note = (
                "Qty Tersedia memakai permintaan cabang terhadap saldo buku gudang ini "
                "— bukan ketersediaan yang dialokasikan per gudang."
            )
            if row.get("qty_tersedia_note"):
                row["qty_tersedia_note"] = (
                    f"{row['qty_tersedia_note']} {scope_note}"
                ).strip()
            else:
                row["qty_tersedia_note"] = scope_note
        else:
            row["physical_qty_scope"] = "BRANCH"
        if not qty_tersedia_valid and baseline_status != BASELINE_RECONCILED:
            existing_note = str(row.get("qty_tersedia_note") or "").strip()
            recon_note = (
                "Qty Tersedia belum valid — saldo fisik belum direkonsiliasi "
                "(Saldo Awal / Stock Opname)."
            )
            if recon_note not in existing_note:
                row["qty_tersedia_note"] = (
                    f"{existing_note} {recon_note}".strip()
                )
        rows.append(row)
    total = len(rows)
    start = (safe_page - 1) * safe_limit
    page_count = (total + safe_limit - 1) // safe_limit if total else 0
    page_items = rows[start:start + safe_limit]
    # Batch enrich only the current page — avoid N+1 on the full catalog.
    await _attach_last_stock_actors(db, page_items)
    return _warehouse_engine_catalog_envelope(
        items=page_items,
        total=total,
        page=safe_page,
        limit=safe_limit,
        page_count=page_count,
        master_item_count=len(masters),
        warehouse_count=len(warehouses),
        catalog_mode=catalog_mode,
        r2_on=r2_on,
        standalone_on=standalone_on,
        selected_warehouse=selected_warehouse,
    )


async def summarize_physical_stock_status(
    db,
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
) -> dict:
    """Read-only status counts for Stock Saat Ini diagnostics.

    Returns aggregate counts and reason buckets only — never item codes, names,
    quantities, actors, or other sensitive row payloads. Does not write.
    """
    catalog = await list_physical_stock_catalog(
        db,
        q=None,
        branch_id=branch_id,
        warehouse_id=warehouse_id,
        page=1,
        limit=1000,
    )
    items = list(catalog.get("items") or [])
    total = int(catalog.get("total") or 0)
    truncated = total > len(items)

    missing_balance_slots = 0
    unreconciled_balance_slots = 0
    items_physical_incomplete = 0
    items_physical_missing = 0
    items_belum_unverified = 0
    items_tersedia_invalid = 0
    items_tersedia_invalid_baseline = 0
    items_tersedia_invalid_belum = 0
    items_physical_ok = 0

    for row in items:
        wh_rows = list(row.get("warehouse_balances") or [])
        if wh_rows:
            slot_missing = 0
            slot_unrec = 0
            for wb in wh_rows:
                present = bool(wb.get("balance_present"))
                status = str(wb.get("baseline_status") or "").strip()
                if not present:
                    slot_missing += 1
                elif status == BASELINE_UNRECONCILED:
                    slot_unrec += 1
            missing_balance_slots += slot_missing
            unreconciled_balance_slots += slot_unrec
            if slot_missing and slot_missing == len(wh_rows):
                items_physical_missing += 1
            elif slot_missing or slot_unrec:
                items_physical_incomplete += 1
            else:
                items_physical_ok += 1
        else:
            # Compatibility / single-scope rows without warehouse_balances.
            status = str(row.get("baseline_status") or "").strip()
            if status == BASELINE_UNRECONCILED:
                items_physical_incomplete += 1
            else:
                items_physical_ok += 1

        belum_status = str(row.get("belum_terkirim_status") or "").strip()
        if belum_status in ("BELUM_TERVERIFIKASI", "CUTOVER_PENDING"):
            items_belum_unverified += 1

        if row.get("qty_tersedia_valid") is False:
            items_tersedia_invalid += 1
            if belum_status in ("BELUM_TERVERIFIKASI", "CUTOVER_PENDING"):
                items_tersedia_invalid_belum += 1
            if str(row.get("baseline_status") or "").strip() != BASELINE_RECONCILED:
                items_tersedia_invalid_baseline += 1

    operational_actions: List[str] = []
    if missing_balance_slots or items_physical_missing or items_physical_incomplete:
        operational_actions.append(
            "Tetapkan Saldo Awal atau konfirmasi Stock Opname per barang×gudang "
            "yang belum punya saldo reconciled — jangan memaksa angka 0 di UI."
        )
    if unreconciled_balance_slots:
        operational_actions.append(
            "Saldo buku unreconciled biasanya dari mutasi outbound tanpa "
            "Saldo Awal/Opname; rekonsiliasi lewat Opname (count=book) atau "
            "penetapan opening yang sah."
        )
    if items_belum_unverified:
        operational_actions.append(
            "Qty Belum Terkirim BELUM_TERVERIFIKASI berasal dari bukti pengiriman "
            "ambigu / cutover legacy — perbaiki provenance pengiriman, bukan "
            "mengarang qty 0."
        )

    return {
        "read_only": True,
        "writes": False,
        "stock_read_mode": catalog.get("stock_read_mode"),
        "r2_authority_active": bool(catalog.get("r2_authority_active")),
        "warehouse_stock_standalone": bool(catalog.get("warehouse_stock_standalone")),
        "effective_branch_id": catalog.get("effective_branch_id") or branch_id,
        "effective_warehouse_id": catalog.get("effective_warehouse_id") or warehouse_id,
        "warehouse_count": int(catalog.get("warehouse_count") or 0),
        "items_scanned": len(items),
        "items_total": total,
        "truncated": truncated,
        "counts": {
            "items_physical_ok": items_physical_ok,
            "items_physical_incomplete": items_physical_incomplete,
            "items_physical_missing": items_physical_missing,
            "warehouse_slots_missing_balance": missing_balance_slots,
            "warehouse_slots_unreconciled": unreconciled_balance_slots,
            "items_belum_terkirim_unverified": items_belum_unverified,
            "items_qty_tersedia_invalid": items_tersedia_invalid,
            "items_qty_tersedia_invalid_baseline": items_tersedia_invalid_baseline,
            "items_qty_tersedia_invalid_belum": items_tersedia_invalid_belum,
        },
        "reason_codes": {
            "BASELINE_MISSING_OR_UNRECONCILED": (
                items_physical_incomplete + items_physical_missing
            ),
            "BELUM_TERVERIFIKASI": items_belum_unverified,
            "QTY_TERSEDIA_INVALID": items_tersedia_invalid,
        },
        "operational_actions": operational_actions,
        # Explicit: diagnostics never invent verified zeros.
        "never_forces_verified_zero": True,
    }


def _movement_sort_key(movement: dict) -> Tuple[str, str]:
    """Newest posted movement wins; occurred_at then created_at, then id."""
    return (
        str(movement.get("occurred_at") or ""),
        str(movement.get("created_at") or ""),
        str(movement.get("id") or ""),
    )


def _actor_display_name(user: Optional[dict]) -> Optional[str]:
    """Public display only — never password, token, or other private fields."""
    if not user:
        return None
    name = str(user.get("name") or "").strip()
    if name:
        return name
    email = str(user.get("email") or "").strip()
    return email or None


async def _attach_last_stock_actors(db, page_items: List[dict]) -> None:
    """Attach updated_at + updated_by_name from the same final posted movement.

    Uses balance.last_movement_id → ledger.actor_id → users display name.
    Pending claims are never consulted. Unknown / missing identity → Tidak tercatat.
    Mutates page_items in place and strips internal enrichment keys.
    """
    movement_ids: List[str] = []
    for row in page_items:
        for mid in row.get("_candidate_movement_ids") or []:
            text = str(mid or "").strip()
            if text:
                movement_ids.append(text)
        for wb in row.get("warehouse_balances") or []:
            text = str(wb.get("_last_movement_id") or "").strip()
            if text:
                movement_ids.append(text)
    unique_ids = list(dict.fromkeys(movement_ids))
    movements_by_id: Dict[str, dict] = {}
    if unique_ids:
        found = await db[LEDGER_COLLECTION].find(
            {"id": {"$in": unique_ids}},
            {
                "_id": 0,
                "id": 1,
                "actor_id": 1,
                "occurred_at": 1,
                "created_at": 1,
                "warehouse_id": 1,
                "master_item_id": 1,
            },
        ).to_list(len(unique_ids))
        movements_by_id = {
            str(doc.get("id")): doc for doc in found if doc.get("id")
        }

    actor_keys: List[str] = []
    for doc in movements_by_id.values():
        actor = str(doc.get("actor_id") or "").strip()
        if actor:
            actor_keys.append(actor)
    unique_actors = list(dict.fromkeys(actor_keys))
    users_by_key: Dict[str, dict] = {}
    if unique_actors:
        user_rows = await db.users.find(
            {"$or": [
                {"id": {"$in": unique_actors}},
                {"email": {"$in": unique_actors}},
            ]},
            {"_id": 0, "id": 1, "name": 1, "email": 1},
        ).to_list(max(len(unique_actors) * 2, 1))
        for user in user_rows:
            uid = str(user.get("id") or "").strip()
            email = str(user.get("email") or "").strip()
            if uid:
                users_by_key[uid] = user
            if email:
                users_by_key[email] = user

    for row in page_items:
        candidate_ids = [
            str(mid).strip()
            for mid in (row.get("_candidate_movement_ids") or [])
            if str(mid or "").strip()
        ]
        candidates = [
            movements_by_id[mid] for mid in candidate_ids if mid in movements_by_id
        ]
        if candidates:
            best = max(candidates, key=_movement_sort_key)
            row["updated_at"] = (
                best.get("occurred_at") or best.get("created_at") or row.get("updated_at")
            )
            actor_id = str(best.get("actor_id") or "").strip()
            display = _actor_display_name(users_by_key.get(actor_id)) if actor_id else None
            row["updated_by_name"] = display or LAST_STOCK_ACTOR_UNKNOWN
        elif row.get("updated_at"):
            # Legacy balance stamp without a provable posted movement actor.
            row["updated_by_name"] = LAST_STOCK_ACTOR_UNKNOWN
        else:
            row["updated_by_name"] = None

        for wb in row.get("warehouse_balances") or []:
            wh_mid = str(wb.pop("_last_movement_id", None) or "").strip()
            movement = movements_by_id.get(wh_mid) if wh_mid else None
            if movement:
                wb["updated_at"] = (
                    movement.get("occurred_at")
                    or movement.get("created_at")
                    or wb.get("updated_at")
                )
                actor_id = str(movement.get("actor_id") or "").strip()
                display = _actor_display_name(users_by_key.get(actor_id)) if actor_id else None
                wb["updated_by_name"] = display or LAST_STOCK_ACTOR_UNKNOWN
            elif wb.get("updated_at"):
                wb["updated_by_name"] = LAST_STOCK_ACTOR_UNKNOWN
            else:
                wb["updated_by_name"] = None

        row.pop("_candidate_movement_ids", None)


async def _catalog_units(db, masters) -> dict:
    ids = [master.get("id") for master in masters if master.get("id")]
    settings = []
    if ids:
        settings = await db[UNIT_SETTINGS_COLLECTION].find(
            {"master_item_id": {"$in": ids}},
            {"_id": 0, "master_item_id": 1, "base_uom": 1},
        ).to_list(len(ids))
    by_item = {
        row.get("master_item_id"): str(row.get("base_uom") or "").strip().upper()
        for row in settings
        if row.get("base_uom")
    }
    units = {}
    for master in masters:
        units[master.get("id")] = by_item.get(master.get("id")) or str(master.get("unit") or "PCS").strip().upper() or "PCS"
    return units
