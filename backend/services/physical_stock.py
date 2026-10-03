"""Append-only physical stock ledger and balance.

Ledger insert and balance mutation commit in one MongoDB transaction.
There is no non-transactional fallback. physical_qty cannot go below zero.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

LEDGER_COLLECTION = "physical_stock_ledger"
BALANCE_COLLECTION = "physical_stock_balances"
MASTER_ITEMS_COLLECTION = "master_items"
BRANCHES_COLLECTION = "branches"
WAREHOUSES_COLLECTION = "warehouses"

MOVEMENT_OPENING_BALANCE = "OPENING_BALANCE"
MOVEMENT_PURCHASE_RECEIPT = "PURCHASE_RECEIPT"
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
    """Outbound quantity would make physical_qty negative."""


class PhysicalStockAbortError(PhysicalStockError):
    """Injected failure used to prove transaction rollback."""


def set_fail_after_balance(enabled: bool) -> None:
    global _fail_after_balance
    _fail_after_balance = bool(enabled)


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


async def _apply_once(db, requested: dict) -> dict:
    ledger = db[LEDGER_COLLECTION]
    balances = db[BALANCE_COLLECTION]
    async with await _client(db).start_session() as session:
        async with session.start_transaction():
            existing = await _existing_movement(db, requested["idempotency_key"], session)
            if existing:
                if not _same_payload(existing, requested):
                    raise PhysicalStockConflictError(
                        "Idempotency key sudah digunakan dengan payload yang berbeda"
                    )
                return _public_movement(existing)

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
            if qty_delta >= 0:
                before_doc = await balances.find_one_and_update(
                    identity,
                    {
                        "$inc": {"physical_qty": qty_delta, "version": 1},
                        "$set": {"last_movement_id": movement_id, "updated_at": created_at},
                        "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": created_at},
                    },
                    upsert=True,
                    return_document=ReturnDocument.BEFORE,
                    session=session,
                )
                balance_before = int((before_doc or {}).get("physical_qty") or 0)
            else:
                before_doc = await balances.find_one_and_update(
                    {**identity, "physical_qty": {"$gte": abs(qty_delta)}},
                    {
                        "$inc": {"physical_qty": qty_delta, "version": 1},
                        "$set": {"last_movement_id": movement_id, "updated_at": created_at},
                    },
                    upsert=False,
                    return_document=ReturnDocument.BEFORE,
                    session=session,
                )
                if not before_doc:
                    raise InsufficientPhysicalStockError(
                        "Stok fisik tidak mencukupi dan tidak boleh menjadi negatif"
                    )
                balance_before = int(before_doc.get("physical_qty") or 0)
            balance_after = balance_before + qty_delta
            if balance_after < 0:
                raise InsufficientPhysicalStockError(
                    "Stok fisik tidak mencukupi dan tidak boleh menjadi negatif"
                )
            if _fail_after_balance:
                raise PhysicalStockAbortError("forced transaction abort")

            doc = {
                **requested,
                "id": movement_id,
                "balance_before": balance_before,
                "balance_after": balance_after,
            }
            await ledger.insert_one(doc, session=session)
            return _public_movement(doc)


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


def _mutation_row(doc: dict, branch: Optional[dict], warehouse: Optional[dict]) -> dict:
    row = {
        "id": doc.get("id"),
        "occurred_at": doc.get("occurred_at"),
        "movement_type": doc.get("movement_type"),
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


def _delivery_qty(line: dict) -> Any:
    if line.get("qty") is not None:
        return line.get("qty")
    if line.get("quantity") is not None:
        return line.get("quantity")
    return line.get("shipped_qty")


def _same_sales_identity(line: dict, ident: dict) -> bool:
    left_accurate = str(line.get("accurate_item_id") or line.get("accurate_id") or "").strip()
    right_accurate = str(ident.get("accurate_item_id") or ident.get("accurate_id") or "").strip()
    if left_accurate and right_accurate and left_accurate == right_accurate:
        return True
    left_master = str(line.get("master_item_id") or line.get("item_id") or "").strip()
    right_master = str(ident.get("master_item_id") or ident.get("item_id") or ident.get("id") or "").strip()
    return bool(left_master and right_master and left_master == right_master)


def _is_operational_branch(branch: dict) -> bool:
    code = str(branch.get("code") or "").strip().upper()
    name = str(branch.get("name") or "").strip().lower()
    return code == "YGY" or "yogyakarta" in name


def resolve_operational_branch(branches: Iterable[dict], warehouse_branch_ids: Iterable[str]) -> Optional[dict]:
    """Current demand belongs to Yogyakarta, once. A single stocked branch is the fallback."""
    rows = [row for row in branches if row.get("id")]
    operational = [row for row in rows if _is_operational_branch(row)]
    if len(operational) > 1:
        coded = [row for row in operational if str(row.get("code") or "").strip().upper() == "YGY"]
        operational = coded or operational[:1]
    if len(operational) == 1:
        return operational[0]
    stocked_ids = {str(branch_id) for branch_id in warehouse_branch_ids if branch_id}
    stocked = [row for row in rows if row.get("id") in stocked_ids]
    if len(stocked) == 1:
        return stocked[0]
    return None


def operational_sales_progress(
    orders: Iterable[dict],
    deliveries: Iterable[dict],
) -> Tuple[Dict[str, float], Dict[str, str], Dict[str, float], Dict[str, str]]:
    """Outstanding sales and print progress still sitting on that outstanding qty.

    Print progress does not change physical stock. Delivered qty leaves outstanding,
    and print already shipped with it is not counted again against open orders.
    """
    from quantity_rules import is_print_line
    from services.stock_engine_v2 import _add, _line_item_identity, identity_keys, qty_num

    order_by_id: Dict[str, dict] = {}
    active_orders: List[dict] = []
    for order in orders:
        if _order_cancelled(order):
            continue
        active_orders.append(order)
        order_id = str(order.get("id") or "").strip()
        if order_id:
            order_by_id[order_id] = order

    delivery_lines: Dict[str, List[dict]] = {}
    for delivery in deliveries:
        if delivery.get("is_cancelled") or delivery.get("cancelled"):
            continue
        order_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()
        if order_id not in order_by_id:
            continue
        lines = delivery.get("line_items") or delivery.get("lines") or []
        delivery_lines.setdefault(order_id, []).extend(line for line in lines if isinstance(line, dict))

    outstanding: Dict[str, float] = {}
    outstanding_alias: Dict[str, str] = {}
    printed: Dict[str, float] = {}
    printed_alias: Dict[str, str] = {}

    for order in active_orders:
        order_id = str(order.get("id") or "").strip()
        lines = list(order.get("line_items") or [])
        shipped = [0.0] * len(lines)
        unindexed: List[Tuple[dict, Any]] = []
        for line in delivery_lines.get(order_id, []):
            qty = _delivery_qty(line)
            try:
                idx = int(line.get("line_idx"))
            except (TypeError, ValueError):
                idx = None
            if idx is not None and 0 <= idx < len(lines):
                shipped[idx] = qty_num(shipped[idx]) + qty_num(qty)
                continue
            ident = _line_item_identity(line)
            if identity_keys(ident):
                unindexed.append((ident, qty))
        for ident, qty in unindexed:
            left = qty_num(qty)
            for idx, line in enumerate(lines):
                if left <= 0:
                    break
                if not _same_sales_identity(line, ident):
                    continue
                room = max(0.0, qty_num(line.get("quantity")) - qty_num(shipped[idx]))
                take = min(room, left)
                shipped[idx] = qty_num(shipped[idx]) + take
                left -= take

        remaining: List[Optional[float]] = []
        for idx, line in enumerate(lines):
            ident = _line_item_identity(line)
            if not identity_keys(ident):
                remaining.append(None)
                continue
            open_qty = max(0.0, qty_num(line.get("quantity")) - qty_num(shipped[idx]))
            remaining.append(open_qty)
            _add(outstanding, outstanding_alias, ident, open_qty)

        print_by_idx: Dict[int, float] = {}
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
            if idx < 0 or idx >= len(lines) or remaining[idx] is None:
                continue
            print_by_idx[idx] = qty_num(print_by_idx.get(idx, 0)) + qty_num(split.get("qty_completed"))
        print_lines = [
            idx for idx, line in enumerate(lines)
            if remaining[idx] is not None and is_print_line(line)
        ]
        if unassigned and len(print_lines) == 1:
            idx = print_lines[0]
            extra = sum(qty_num(split.get("qty_completed")) for split in unassigned)
            print_by_idx[idx] = qty_num(print_by_idx.get(idx, 0)) + extra

        for idx, line in enumerate(lines):
            open_qty = remaining[idx]
            if open_qty is None:
                continue
            applied = min(qty_num(print_by_idx.get(idx, 0)), qty_num(open_qty))
            if applied <= 0:
                continue
            _add(printed, printed_alias, _line_item_identity(line), applied)

    return outstanding, outstanding_alias, printed, printed_alias


def _demand_for_master(master: dict, progress) -> dict:
    from services.stock_engine_v2 import _get, compute_belum_dicetak, qty_num

    outstanding, outstanding_alias, printed, printed_alias = progress
    ident = {
        "id": master.get("id"),
        "master_item_id": master.get("id"),
        "accurate_item_id": master.get("accurate_item_id"),
    }
    order_qty = qty_num(_get(outstanding, outstanding_alias, ident))
    printed_qty = min(qty_num(_get(printed, printed_alias, ident)), order_qty)
    return {
        "order_qty": order_qty,
        "printed_qty": printed_qty,
        "belum_dicetak": compute_belum_dicetak(order_qty, printed_qty),
    }


async def _sales_progress(db):
    orders = await db.orders.find({}, {"_id": 0}).to_list(20000)
    deliveries = await db.sales_deliveries.find({}, {"_id": 0}).to_list(20000)
    return operational_sales_progress(orders, deliveries)


def _operational_branch_payload(branch: Optional[dict]) -> Optional[dict]:
    if not branch:
        return None
    return {
        "id": branch.get("id") or "",
        "code": branch.get("code") or "",
        "name": branch.get("name") or "",
    }


def _catalog_page(page: int, limit: int) -> Tuple[int, int]:
    safe_page = page if isinstance(page, int) and page > 0 else 1
    safe_limit = limit if isinstance(limit, int) and limit > 0 else 50
    return safe_page, min(safe_limit, _MAX_READ_LIMIT)


async def list_physical_stock_catalog(
    db,
    *,
    q: Optional[str] = None,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """Item + branch stock. Physical qty is the sum of that branch's warehouses.

    Outstanding sales orders attach once, to the operational branch. Stock Saat Ini
    is that physical sum minus outstanding qty and may be negative. Print progress
    does not reduce physical stock or Stock Saat Ini. Missing balances stay 0.
    """
    branch = await _require_branch_any(db, branch_id) if branch_id else None
    warehouse = await _require_warehouse_any(db, warehouse_id) if warehouse_id else None
    if branch and warehouse and warehouse.get("branch_id") != branch.get("id"):
        raise PhysicalStockValidationError("Gudang tidak berada pada cabang ini")
    warehouse_query: Dict[str, Any] = {}
    if branch:
        warehouse_query["branch_id"] = branch["id"]
    warehouses = await db[WAREHOUSES_COLLECTION].find(warehouse_query, {"_id": 0}).sort(
        [("code", 1), ("id", 1)]
    ).to_list(500)
    branch_ids = {row.get("branch_id") for row in warehouses if row.get("branch_id")}
    branches, _unused = await _location_maps(db, branch_ids, [])
    all_branches = await db[BRANCHES_COLLECTION].find({}, {"_id": 0, "id": 1, "code": 1, "name": 1}).to_list(500)
    operational = resolve_operational_branch(all_branches, branch_ids)
    operational_id = (operational or {}).get("id")
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
    empty = {
        "items": [],
        "total": 0,
        "page": safe_page,
        "limit": safe_limit,
        "page_count": 0,
        "master_item_count": len(masters),
        "warehouse_count": len(warehouses),
        "operational_branch": _operational_branch_payload(operational),
    }
    if not masters or not warehouses:
        return empty
    units = await _catalog_units(db, masters)
    progress = await _sales_progress(db)
    balance_docs = await db[BALANCE_COLLECTION].find(
        {
            "master_item_id": {"$in": [master["id"] for master in masters]},
            "warehouse_id": {"$in": [wh["id"] for wh in warehouses]},
        },
        {"_id": 0},
    ).to_list(max(len(masters) * len(warehouses), 1))
    by_branch: Dict[str, List[dict]] = {}
    for wh in warehouses:
        by_branch.setdefault(wh.get("branch_id") or "", []).append(wh)
    balances = {}
    for doc in balance_docs:
        balances.setdefault((doc.get("master_item_id"), doc.get("branch_id")), []).append(doc)
    from services.stock_engine_v2 import compute_stock_current, qty_num

    rows = []
    for master in masters:
        demand = _demand_for_master(master, progress)
        for branch_key in sorted(by_branch, key=lambda key: ((branches.get(key) or {}).get("code") or "", key or "")):
            branch_doc = branches.get(branch_key) or {}
            docs = balances.get((master.get("id"), branch_key), [])
            physical = sum(qty_num(doc.get("physical_qty")) for doc in docs)
            updated = [doc.get("updated_at") for doc in docs if doc.get("updated_at")]
            on_operational = bool(operational_id) and branch_key == operational_id
            order_qty = demand["order_qty"] if on_operational else 0
            printed_qty = demand["printed_qty"] if on_operational else 0
            belum = demand["belum_dicetak"] if on_operational else 0
            rows.append({
                "master_item_id": master.get("id"),
                "item_code": master.get("item_code") or "",
                "item_name": master.get("item_name") or "",
                "accurate_item_id": str(master.get("accurate_item_id") or ""),
                "base_unit": units.get(master.get("id")) or "PCS",
                "branch_id": branch_key,
                "branch_code": branch_doc.get("code") or "",
                "branch_name": branch_doc.get("name") or "",
                "physical_qty": qty_num(physical),
                "order_qty": order_qty,
                "printed_qty": printed_qty,
                "belum_dicetak": belum,
                "stock_current": compute_stock_current(physical, order_qty),
                "updated_at": max(updated) if updated else None,
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
        "operational_branch": _operational_branch_payload(operational),
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
