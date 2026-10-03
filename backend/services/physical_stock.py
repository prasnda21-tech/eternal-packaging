"""Append-only physical stock ledger and balance.

Ledger insert and balance mutation commit in one MongoDB transaction.
There is no non-transactional fallback. physical_qty cannot go below zero.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

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


def _as_qty(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value == 0:
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
            if qty_delta > 0:
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
    qty_delta = _as_qty((raw or {}).get("qty_delta"))
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
