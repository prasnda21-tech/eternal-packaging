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
    """Resolve R2 delivery location + master identity; never block on book qty.

    Warehouse/master identity must be valid (no guessing). Missing balance is
    unreconciled baseline — delivery may proceed; warnings describe uncertainty.
    Zero book qty is valid and not a readiness failure.
    """
    if not isinstance(order, dict):
        raise PhysicalStockValidationError("Pesanan tidak ditemukan")
    wh_id = str(warehouse_id or "").strip()
    if not wh_id:
        raise PhysicalStockValidationError(
            "Pilih gudang sebelum konfirmasi terkirim saat stok R2 aktif"
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
            "Baris pengiriman tidak dapat dipetakan ke master item untuk stok R2"
        )
    return str(branch["id"]), str(warehouse["id"]), warnings


def _purchase_receipt_request(receipt: dict, line: dict, *, actor_id: str) -> dict:
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
        },
    }


async def apply_purchase_receipt_lines(db, session, receipt: dict, *, actor_id: str) -> List[dict]:
    """Post every PURCHASE_RECEIPT line inside the caller's transaction."""
    lines = receipt.get("lines") if isinstance(receipt.get("lines"), list) else []
    if not lines:
        raise PhysicalStockValidationError("Minimal satu baris penerimaan")
    movements = []
    for line in lines:
        movements.append(await _apply_movement_in_session(
            db,
            _purchase_receipt_request(receipt, line, actor_id=actor_id),
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

    Non-zero differences post qty movements. Zero differences still mark the
    warehouse balance baseline as reconciled when a balance row exists
    (count equals book, including both zero) — without inventing a qty change.
    """
    session_id = str(session_doc.get("id") or "")
    movements = []
    for line in lines:
        delta = int(line.get("difference_qty") or 0)
        if delta == 0:
            identity = {
                "master_item_id": str(line.get("master_item_id") or "").strip(),
                "branch_id": str(session_doc.get("branch_id") or "").strip(),
                "warehouse_id": str(session_doc.get("warehouse_id") or "").strip(),
            }
            if all(identity.values()):
                await db[BALANCE_COLLECTION].update_one(
                    identity,
                    {
                        "$set": {
                            "baseline_status": BASELINE_RECONCILED,
                            "updated_at": occurred_at,
                        }
                    },
                    session=mongo_session,
                )
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
    """Append one reversal inside the caller's transaction. The original row is unchanged."""
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
        except (PhysicalStockValidationError, PhysicalStockConflictError, InsufficientPhysicalStockError):
            raise
        except PyMongoError as exc:
            last_error = exc
            if not _retryable(exc):
                raise
    raise PhysicalStockUnavailableError(
        "Transaksi pembatalan pengiriman stok fisik gagal setelah percobaan ulang"
    ) from last_error


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
    identity = {
        token
        for token in (
            str(delivery.get("id") or "").strip(),
            str(delivery.get("delivery_id") or "").strip(),
            str(delivery.get("sj_number") or "").strip(),
        )
        if token
    }
    confirmation_ids = set()
    if identity:
        linked = await db[SALES_DELIVERY_CONFIRMATIONS].find(
            {
                "$or": [
                    {"sales_delivery_id": {"$in": list(identity)}},
                    {"id": {"$in": list(identity)}},
                ]
            },
            {"_id": 0, "id": 1},
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
        ).to_list(500)
        for row in rows:
            cid = str((row.get("metadata") or {}).get("confirmation_id") or "").strip()
            if cid:
                confirmation_ids.add(cid)

    po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()
    if po_id and not confirmation_ids:
        # Fallback: order-level r2 confirmation ids when SJ link was not stored.
        order = await db.orders.find_one(
            {"id": po_id},
            {"_id": 0, "r2_confirmed_delivery_ids": 1, "delivery_confirmations": 1},
        )
        if order:
            for cid in order.get("r2_confirmed_delivery_ids") or []:
                if cid:
                    confirmation_ids.add(str(cid))
            for conf in order.get("delivery_confirmations") or []:
                if not isinstance(conf, dict):
                    continue
                for field in ("sales_delivery_id", "shipment_id", "surat_jalan_id", "sj_number", "delivery_id"):
                    token = str(conf.get(field) or "").strip()
                    if token and token in identity and conf.get("id"):
                        confirmation_ids.add(str(conf["id"]))
                        break

    results = []
    for cid in sorted(confirmation_ids):
        results.append(
            await reverse_confirmed_sales_delivery(
                db, confirmation_id=cid, actor_id=actor_id, reason=reason,
            )
        )
    return {
        "reversed_confirmations": results,
        "confirmation_ids": sorted(confirmation_ids),
    }


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
    """The single operational sales branch is Yogyakarta. Code YGY wins when several match."""
    rows = [row for row in branches if row.get("id") and _is_operational_branch(row)]
    coded = [row for row in rows if str(row.get("code") or "").strip().upper() == "YGY"]
    if coded:
        return coded[0]
    return rows[0] if rows else None


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


def aggregate_operational_orders(
    orders: Iterable[dict],
    accurate_owners: Optional[Dict[str, str]] = None,
) -> Dict[str, Dict[str, float]]:
    """Sum active ordered qty and ready qty per master_item_id.

    Print lines: ready = operator qty_completed (unassigned splits only attach to
    the sole print line when exactly one print line is identified).
    Non-print lines: ready = ordered qty (siap kirim) without writing completed
    or packed fields on the order. Cancelled orders contribute nothing.
    operational_availability floors the buckets.
    """
    from quantity_rules import is_print_line, operational_ready_qty_for_line

    owners = accurate_owners or {}
    totals: Dict[str, Dict[str, float]] = {}

    def add(master_id: str, ordered: float, completed: float) -> None:
        if not master_id:
            return
        slot = totals.setdefault(master_id, {"ordered_qty": 0.0, "completed_qty": 0.0})
        slot["ordered_qty"] = _qty_num(slot["ordered_qty"]) + _qty_num(ordered)
        slot["completed_qty"] = _qty_num(slot["completed_qty"]) + _qty_num(completed)

    for order in orders:
        if _order_cancelled(order):
            continue
        lines = list(order.get("line_items") or [])
        master_ids = [_line_master_id(line, owners) for line in lines]
        # Attach unassigned operator splits to the sole print line (existing rule).
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
            if not is_print_line(lines[idx]):
                continue
            completed_by_idx[idx] = _qty_num(completed_by_idx.get(idx, 0)) + _qty_num(split.get("qty_completed"))
        identified = [idx for idx, master_id in enumerate(master_ids) if master_id and is_print_line(lines[idx])]
        if unassigned and len(identified) == 1:
            idx = identified[0]
            extra = sum(_qty_num(split.get("qty_completed")) for split in unassigned)
            completed_by_idx[idx] = _qty_num(completed_by_idx.get(idx, 0)) + extra
        for idx, line in enumerate(lines):
            if not is_print_line(line):
                ready = operational_ready_qty_for_line(order, idx)
            else:
                ordered = _qty_num(line.get("quantity"))
                ready = min(_qty_num(completed_by_idx.get(idx, 0)), ordered)
            add(master_ids[idx], _qty_num(line.get("quantity")), ready)
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


async def _r2_confirmed_delivery_qty(db, master_ids: List[str]) -> Dict[str, float]:
    """Delivered qty that already left R2 physical stock (net of reversals).

    The SALES_DELIVERY ledger movement is the proof. A legacy sales_deliveries
    row, surat jalan, or loading record does not reduce Qty Belum Terkirim.
    Reversed SALES_DELIVERY rows are excluded.
    """
    if not master_ids:
        return {}
    movements = await db[LEDGER_COLLECTION].find(
        {
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "master_item_id": {"$in": master_ids},
        },
        {"_id": 0, "id": 1, "master_item_id": 1, "qty_delta": 1},
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

    When r2_authority_active is FALSE: read-only compatibility adapter
    (legacy physical state + orders + sales deliveries). No R2 writes.

    When r2_authority_active is TRUE: Stock fisik is the sum of R2 balances on
    operational warehouses for ONE branch (default YGY). Never aggregates all
    branches. Optional warehouse_id further narrows within that branch.
    """
    from services.r2_go_live import is_r2_authority_active
    from services.stock_compatibility_read import list_compatibility_stock_catalog

    if not await is_r2_authority_active(db):
        return await list_compatibility_stock_catalog(db, q=q, page=page, limit=limit)

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
    safe_page, safe_limit = _catalog_page(page, limit)
    if not masters:
        return {
            "items": [],
            "total": 0,
            "page": safe_page,
            "limit": safe_limit,
            "page_count": 0,
            "master_item_count": 0,
            "warehouse_count": len(warehouses),
        }
    units = await _catalog_units(db, masters)
    owners = await _unique_accurate_owners(db)
    orders = await db.orders.find({}, {"_id": 0}).to_list(20000)
    progress = aggregate_operational_orders(orders, owners)
    master_ids = [master["id"] for master in masters if master.get("id")]
    delivered = await _r2_confirmed_delivery_qty(db, master_ids)
    from services.r2_go_live import get_delivery_cutover_baseline

    cutover = await get_delivery_cutover_baseline(db)
    baseline_by_item = cutover.get("baseline_by_master_item_id") or {} if cutover.get("initialized") else {}
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
    rows = []
    for master in masters:
        docs = balances.get(master.get("id"), [])
        physical = sum(_qty_num(doc.get("physical_qty")) for doc in docs)
        updated = [doc.get("updated_at") for doc in docs if doc.get("updated_at")]
        demand = progress.get(master.get("id")) or {"ordered_qty": 0.0, "completed_qty": 0.0}
        mid = master.get("id")
        effective_delivered = _qty_num(delivered.get(mid, 0)) + _qty_num(baseline_by_item.get(str(mid), 0))
        buckets = operational_availability(
            physical_qty=physical,
            ordered_qty=demand["ordered_qty"],
            completed_qty=demand["completed_qty"],
            r2_delivered_qty=effective_delivered,
        )
        baseline_statuses = [
            str(doc.get("baseline_status") or BASELINE_RECONCILED).strip() or BASELINE_RECONCILED
            for doc in docs
        ]
        if not docs:
            baseline_status = None
        elif any(status == BASELINE_UNRECONCILED for status in baseline_statuses):
            baseline_status = BASELINE_UNRECONCILED
        else:
            baseline_status = BASELINE_RECONCILED
        rows.append({
            "master_item_id": master.get("id"),
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or "",
            "accurate_item_id": str(master.get("accurate_item_id") or ""),
            "base_unit": units.get(master.get("id")) or "PCS",
            "physical_qty": buckets["physical_qty"],
            "qty_pesanan": buckets["qty_pesanan"],
            "qty_belum_terkirim": buckets["qty_belum_terkirim"],
            "qty_tersedia": buckets["qty_tersedia"],
            "updated_at": max(updated) if updated else None,
            "belum_terkirim_status": "READY",
            "stock_read_mode": "r2",
            "baseline_status": baseline_status,
            "baseline_label": (
                BASELINE_UNRECONCILED_LABEL if baseline_status == BASELINE_UNRECONCILED else None
            ),
            "saldo_buku_negatif": buckets["physical_qty"] < 0,
            "physical_qty_is_book": True,
        })
    total = len(rows)
    start = (safe_page - 1) * safe_limit
    page_count = (total + safe_limit - 1) // safe_limit if total else 0
    return {
        "items": rows[start:start + safe_limit],
        "total": total,
        "page": safe_page,
        "limit": safe_limit,
        "page_count": page_count,
        "master_item_count": len(masters),
        "warehouse_count": len(warehouses),
        "stock_read_mode": "r2",
        "r2_authority_active": True,
        "physical_qty_scope": "BRANCH",
        "branch_filter_affects_physical_qty": True,
        "physical_qty_note": (
            "Qty fisik resmi dari physical_stock_balances pada cabang yang dipilih."
        ),
    }


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
