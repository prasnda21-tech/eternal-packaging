"""Warehouse stock mutations that are safe on MongoDB standalone.

Reuses physical_stock_balances + physical_stock_ledger (same identity as R2)
but NEVER opens a multi-document transaction. Crash safety uses the proven
pending-event claim → unique ledger insert → CAS finalize pattern from the
legacy physical_stock_engine / product_stock_state flow.

Authority rules:
- Disabled when r2_authority_active is True (R2 remains exclusive).
- Enabled only via app_settings key warehouse_stock_standalone.enabled
  (default False — production read path unchanged until Owner flips the flag).
- Does not call or bypass require_transaction_support / require_r2_authority_active.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.physical_stock import (
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BASELINE_UNRECONCILED,
    LEDGER_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_PURCHASE_RETURN,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    PhysicalStockConflictError,
    PhysicalStockRetryError,
    PhysicalStockUnavailableError,
    PhysicalStockValidationError,
    _as_physical_qty,
    _as_text,
    _check_sign,
    _public_movement,
    _purchase_receipt_request,
    _same_adjustment,
    _same_payload,
    _stock_opname_adjustment_request,
    _validate_masters,
    opening_reference_id,
    now_iso,
)

SETTINGS_KEY = "warehouse_stock_standalone"
MODE_WAREHOUSE_STANDALONE = "warehouse_standalone"

_STATUS_PARTIAL = "PARTIAL"
_STATUS_CONFIRMED = "CONFIRMED"
_STATUS_FAILED = "FAILED"


def default_settings() -> Dict[str, Any]:
    return {
        "key": SETTINGS_KEY,
        "enabled": False,
        "updated_at": None,
        "updated_by": None,
    }


def sanitize_settings(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    base = default_settings()
    if not isinstance(doc, dict):
        return base
    return {
        "key": SETTINGS_KEY,
        "enabled": bool(doc.get("enabled")) if doc.get("enabled") is not None else False,
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


async def get_warehouse_stock_standalone_settings(db) -> Dict[str, Any]:
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return sanitize_settings(doc)


async def set_warehouse_stock_standalone_enabled(
    db,
    *,
    enabled: bool,
    user: Optional[dict] = None,
) -> Dict[str, Any]:
    actor = None
    if isinstance(user, dict):
        actor = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": user.get("name") or user.get("email") or "User",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }
    saved = {
        "key": SETTINGS_KEY,
        "enabled": bool(enabled),
        "updated_at": now_iso(),
        "updated_by": actor,
    }
    await db.app_settings.update_one({"key": SETTINGS_KEY}, {"$set": saved}, upsert=True)
    return sanitize_settings(saved)


async def is_warehouse_stock_standalone_active(db) -> bool:
    """True only when Owner flag is on AND R2 authority is off."""
    from services.r2_go_live import is_r2_authority_active

    if await is_r2_authority_active(db):
        return False
    settings = await get_warehouse_stock_standalone_settings(db)
    return bool(settings.get("enabled"))


async def require_warehouse_stock_standalone(db) -> None:
    from fastapi import HTTPException
    from services.r2_go_live import is_r2_authority_active

    if await is_r2_authority_active(db):
        raise HTTPException(
            409,
            "Warehouse standalone dinonaktifkan karena r2_authority_active sudah ON.",
        )
    if not await is_warehouse_stock_standalone_active(db):
        raise HTTPException(
            409,
            "Warehouse stock standalone belum diaktifkan "
            "(app_settings.warehouse_stock_standalone.enabled).",
        )


def _identity(requested: dict) -> Dict[str, str]:
    return {
        "master_item_id": requested["master_item_id"],
        "branch_id": requested["branch_id"],
        "warehouse_id": requested["warehouse_id"],
    }


async def _existing_by_key(db, idempotency_key: str) -> Optional[dict]:
    return await db[LEDGER_COLLECTION].find_one(
        {"idempotency_key": idempotency_key},
        {"_id": 0},
    )


async def recover_pending_balance(db, identity: dict) -> Optional[dict]:
    """Complete or clear an interrupted pending_event on one balance row."""
    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    if not bal:
        return None
    pending = bal.get("pending_event")
    if not isinstance(pending, dict):
        return None
    ev_id = str(pending.get("event_id") or "").strip()
    key = str(pending.get("idempotency_key") or "").strip()
    if not ev_id or not key:
        await db[BALANCE_COLLECTION].update_one(
            {**identity, "pending_event.event_id": ev_id or None},
            {"$unset": {"pending_event": ""}},
        )
        return await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})

    existing = await _existing_by_key(db, key)
    if existing:
        # Ledger committed — finish qty apply if still pending.
        return await _finalize_from_pending(db, identity, pending, existing)

    # No ledger — release claim so a retry can proceed.
    await db[BALANCE_COLLECTION].update_one(
        {**identity, "pending_event.event_id": ev_id},
        {"$unset": {"pending_event": ""}},
    )
    return await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})


async def _finalize_from_pending(
    db,
    identity: dict,
    pending: dict,
    movement: dict,
) -> dict:
    ev_id = str(pending.get("event_id") or "")
    qty_delta = int(pending.get("qty_delta") or movement.get("qty_delta") or 0)
    movement_id = str(movement.get("id") or pending.get("movement_id") or "")
    stamp = now_iso()
    establishes = str(movement.get("movement_type") or "") in (
        MOVEMENT_OPENING_BALANCE,
        MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        MOVEMENT_MANUAL_ADJUSTMENT,
    )
    set_fields = {
        "last_movement_id": movement_id,
        "updated_at": stamp,
    }
    if establishes:
        set_fields["baseline_status"] = BASELINE_RECONCILED
    updated = await db[BALANCE_COLLECTION].find_one_and_update(
        {**identity, "pending_event.event_id": ev_id},
        {
            "$inc": {"physical_qty": qty_delta, "version": 1},
            "$set": set_fields,
            "$unset": {"pending_event": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        # Already finalized by a concurrent recover.
        return await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0}) or {}
    return updated


async def apply_standalone_movement(db, raw: dict, *, actor_id: str) -> dict:
    """Post one warehouse movement without a multi-document transaction."""
    movement_type = _as_text((raw or {}).get("movement_type"), "movement_type")
    qty_delta = int((raw or {}).get("qty_delta") or 0)
    if movement_type == MOVEMENT_OPENING_BALANCE:
        if qty_delta < 0:
            raise PhysicalStockValidationError("Saldo awal tidak boleh negatif")
    elif movement_type in (MOVEMENT_MANUAL_ADJUSTMENT, MOVEMENT_STOCK_OPNAME_ADJUSTMENT, MOVEMENT_REVERSAL):
        pass  # sign checked below / by callers
    else:
        _check_sign(movement_type, qty_delta)
        if qty_delta == 0:
            raise PhysicalStockValidationError("qty_delta harus bilangan bulat selain nol")

    idempotency_key = _as_text((raw or {}).get("idempotency_key"), "idempotency_key")
    existing = await _existing_by_key(db, idempotency_key)
    created_at = str((raw or {}).get("created_at") or "").strip() or now_iso()
    occurred_at = str((raw or {}).get("occurred_at") or "").strip() or created_at
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
        "idempotency_key": idempotency_key,
    }
    metadata = (raw or {}).get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise PhysicalStockValidationError("metadata harus objek")
    if metadata:
        requested["metadata"] = metadata

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
        session=None,
    )

    identity = _identity(requested)
    await recover_pending_balance(db, identity)

    if movement_type == MOVEMENT_OPENING_BALANCE:
        prior_ledger = await db[LEDGER_COLLECTION].find_one(identity, {"_id": 1})
        if prior_ledger:
            raise PhysicalStockConflictError(
                "Saldo awal ditolak karena identitas ini sudah memiliki mutasi stok fisik"
            )
        prior_balance = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 1})
        if prior_balance and prior_balance.get("pending_event") is None:
            # Existing balance without pending — opening only if never used.
            if int(prior_balance.get("physical_qty") or 0) != 0 or prior_balance.get("baseline_status") == BASELINE_RECONCILED:
                raise PhysicalStockConflictError(
                    "Saldo awal ditolak karena saldo fisik sudah ada"
                )

    # Replay check again after recover.
    existing = await _existing_by_key(db, idempotency_key)
    if existing:
        if not _same_payload(existing, requested):
            raise PhysicalStockConflictError(
                "Idempotency key sudah digunakan dengan payload yang berbeda"
            )
        return _public_movement(existing)

    movement_id = str(uuid.uuid4())
    event_id = f"standalone:{idempotency_key}"
    claim = {
        "event_id": event_id,
        "idempotency_key": idempotency_key,
        "movement_id": movement_id,
        "qty_delta": qty_delta,
        "movement_type": movement_type,
        "stage": "CLAIMED",
        "claimed_at": created_at,
        "actor_id": requested["actor_id"],
    }

    # Claim: set pending_event only when free. Upsert creates unreconciled shell.
    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    if bal and bal.get("pending_event"):
        raise PhysicalStockRetryError("Saldo fisik sedang diklaim proses lain")

    if bal is None:
        try:
            await db[BALANCE_COLLECTION].insert_one(
                {
                    "id": str(uuid.uuid4()),
                    **identity,
                    "physical_qty": 0,
                    "version": 0,
                    "baseline_status": BASELINE_UNRECONCILED,
                    "pending_event": claim,
                    "created_at": created_at,
                    "updated_at": created_at,
                }
            )
        except DuplicateKeyError:
            raise PhysicalStockRetryError("Saldo fisik dibuat bersamaan — coba lagi")
        balance_before = 0
    else:
        claimed = await db[BALANCE_COLLECTION].find_one_and_update(
            {
                **identity,
                "$or": [
                    {"pending_event": None},
                    {"pending_event": {"$exists": False}},
                ],
            },
            {"$set": {"pending_event": claim, "updated_at": created_at}},
            return_document=ReturnDocument.BEFORE,
        )
        if claimed is None:
            raise PhysicalStockRetryError("Saldo fisik sedang diklaim proses lain")
        balance_before = int(claimed.get("physical_qty") or 0)

    balance_after = balance_before + qty_delta
    establishes = movement_type in (
        MOVEMENT_OPENING_BALANCE,
        MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        MOVEMENT_MANUAL_ADJUSTMENT,
    )
    meta = dict(requested.get("metadata") or {})
    if establishes:
        meta["baseline_status"] = BASELINE_RECONCILED
    doc = {
        **requested,
        "id": movement_id,
        "balance_before": balance_before,
        "balance_after": balance_after,
        "engine": MODE_WAREHOUSE_STANDALONE,
    }
    if meta:
        doc["metadata"] = meta

    try:
        await db[LEDGER_COLLECTION].insert_one(doc)
    except DuplicateKeyError:
        raced = await _existing_by_key(db, idempotency_key)
        await db[BALANCE_COLLECTION].update_one(
            {**identity, "pending_event.event_id": event_id},
            {"$unset": {"pending_event": ""}},
        )
        if raced and _same_payload(raced, requested):
            return _public_movement(raced)
        raise PhysicalStockConflictError(
            "Idempotency key sudah digunakan dengan payload yang berbeda"
        )

    # Optional test hook: crash after ledger, before finalize.
    if _fail_after_ledger:
        raise PhysicalStockUnavailableError("forced standalone abort after ledger")

    set_fields = {
        "last_movement_id": movement_id,
        "updated_at": created_at,
    }
    if establishes:
        set_fields["baseline_status"] = BASELINE_RECONCILED

    finalized = await db[BALANCE_COLLECTION].find_one_and_update(
        {**identity, "pending_event.event_id": event_id},
        {
            "$inc": {"physical_qty": qty_delta, "version": 1},
            "$set": set_fields,
            "$unset": {"pending_event": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if finalized is None:
        # Concurrent recover may have finished — verify ledger + balance.
        await recover_pending_balance(db, identity)
        bal_now = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0}) or {}
        if int(bal_now.get("physical_qty") or 0) != balance_after and bal_now.get("pending_event"):
            raise PhysicalStockUnavailableError("Finalize standalone gagal — pending masih ada")
    return _public_movement(doc)


_fail_after_ledger = False


def set_fail_after_ledger(flag: bool) -> None:
    global _fail_after_ledger
    _fail_after_ledger = bool(flag)


async def post_opening_balance_standalone(db, item_id: str, raw: dict, *, actor_id: str) -> dict:
    payload = raw or {}
    branch_id = _as_text(payload.get("branch_id"), "branch_id")
    warehouse_id = _as_text(payload.get("warehouse_id"), "warehouse_id")
    physical_qty = _as_physical_qty(payload.get("physical_qty"))
    reason = str(payload.get("reason") or "").strip() or "Saldo awal"
    movement = await apply_standalone_movement(
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
        "engine": MODE_WAREHOUSE_STANDALONE,
    }


async def adjust_to_target_standalone(db, item_id: str, raw: dict, *, actor_id: str) -> dict:
    payload = raw or {}
    master_item_id = _as_text(item_id, "master_item_id")
    branch_id = _as_text(payload.get("branch_id"), "branch_id")
    warehouse_id = _as_text(payload.get("warehouse_id"), "warehouse_id")
    target = _as_physical_qty(payload.get("target_physical_qty"))
    reason = _as_text(payload.get("reason"), "reason")
    idempotency_key = _as_text(payload.get("idempotency_key"), "idempotency_key")

    existing = await _existing_by_key(db, idempotency_key)
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
        return {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "previous_physical_qty": int(existing.get("balance_before") or 0),
            "target_physical_qty": target,
            "qty_delta": int(existing.get("qty_delta") or 0),
            "physical_qty": int(existing.get("balance_after") if existing.get("balance_after") is not None else target),
            "movement_id": existing.get("id"),
            "changed": True,
            "engine": MODE_WAREHOUSE_STANDALONE,
        }

    identity = {
        "master_item_id": master_item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
    }
    await recover_pending_balance(db, identity)
    current_doc = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    current = int((current_doc or {}).get("physical_qty") or 0)
    delta = target - current
    if delta == 0 and current_doc is not None:
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
            "engine": MODE_WAREHOUSE_STANDALONE,
        }

    movement = await apply_standalone_movement(
        db,
        {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_MANUAL_ADJUSTMENT,
            "qty_delta": delta,
            "reference_type": MOVEMENT_MANUAL_ADJUSTMENT,
            "reference_id": idempotency_key,
            "reason": reason,
            "idempotency_key": idempotency_key,
            "metadata": {"target_physical_qty": target, "baseline_status": BASELINE_RECONCILED},
        },
        actor_id=actor_id,
    )
    return {
        "master_item_id": master_item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
        "previous_physical_qty": int(movement.get("balance_before") or 0),
        "target_physical_qty": target,
        "qty_delta": int(movement.get("qty_delta") or 0),
        "physical_qty": int(movement.get("balance_after") if movement.get("balance_after") is not None else target),
        "movement_id": movement.get("id"),
        "changed": True,
        "engine": MODE_WAREHOUSE_STANDALONE,
    }


async def apply_purchase_receipt_lines_standalone(
    db, receipt: dict, *, actor_id: str
) -> List[dict]:
    lines = receipt.get("lines") if isinstance(receipt.get("lines"), list) else []
    if not lines:
        raise PhysicalStockValidationError("Minimal satu baris penerimaan")
    movements = []
    for line in lines:
        req = _purchase_receipt_request(receipt, line, actor_id=actor_id)
        movements.append(await apply_standalone_movement(db, req, actor_id=actor_id))
    return movements


async def apply_sales_delivery_line_standalone(
    db,
    *,
    master_item_id: str,
    branch_id: str,
    warehouse_id: str,
    qty: int,
    confirmation_id: str,
    line_idx: int,
    actor_id: str,
    reason: str = "Konfirmasi terkirim",
) -> dict:
    if not isinstance(qty, int) or qty <= 0:
        raise PhysicalStockValidationError("Qty pengiriman harus bilangan bulat > 0")
    return await apply_standalone_movement(
        db,
        {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "qty_delta": -qty,
            "reference_type": "sales_delivery_confirmation",
            "reference_id": f"{confirmation_id}:{line_idx}",
            "reason": reason,
            "idempotency_key": f"sales-delivery:{confirmation_id}:{line_idx}:{master_item_id}",
            "metadata": {"confirmation_id": confirmation_id, "line_idx": line_idx},
        },
        actor_id=actor_id,
    )


async def apply_opname_lines_standalone(
    db,
    session_doc: dict,
    lines: List[dict],
    *,
    actor_id: str,
    occurred_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Post opname adjustments one line at a time. Returns PARTIAL if some fail."""
    stamp = occurred_at or now_iso()
    results = []
    failures = []
    for line in lines:
        line_id = str(line.get("line_id") or "")
        diff = int(line.get("difference_qty") or 0)
        if diff == 0:
            results.append({"line_id": line_id, "status": "SKIPPED", "movement": None})
            continue
        req = _stock_opname_adjustment_request(
            session_doc, line, actor_id=actor_id, occurred_at=stamp,
        )
        # Opname request uses target via difference embedded in qty_delta.
        try:
            mov = await apply_standalone_movement(db, req, actor_id=actor_id)
            results.append({"line_id": line_id, "status": "POSTED", "movement": mov})
        except Exception as exc:  # noqa: BLE001 — capture per-line; session stays PARTIAL
            failures.append({"line_id": line_id, "status": _STATUS_FAILED, "error": str(exc)})
            results.append({"line_id": line_id, "status": _STATUS_FAILED, "error": str(exc)})

    posted = sum(1 for row in results if row.get("status") == "POSTED")
    failed = sum(1 for row in results if row.get("status") == _STATUS_FAILED)
    if failed and posted:
        status = _STATUS_PARTIAL
    elif failed and not posted:
        status = _STATUS_FAILED
    else:
        status = _STATUS_CONFIRMED
    return {
        "status": status,
        "posted_count": posted,
        "failed_count": failed,
        "lines": results,
        "engine": MODE_WAREHOUSE_STANDALONE,
    }
