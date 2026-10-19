"""Warehouse stock mutations that are safe on MongoDB standalone.

Reuses physical_stock_balances + physical_stock_ledger (same identity as R2)
but NEVER opens a multi-document transaction. Crash safety uses pending-event
claim → unique ledger insert → CAS finalize.

Authority rules:
- Disabled when r2_authority_active is True (R2 remains exclusive).
- Enabled only via app_settings key warehouse_stock_standalone.enabled
  (default False).
- Does not call or bypass require_transaction_support / require_r2_authority_active.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.physical_stock import (
    BALANCE_COLLECTION,
    BASELINE_RECONCILED,
    BASELINE_UNRECONCILED,
    BASELINE_UNRECONCILED_LABEL,
    LEDGER_COLLECTION,
    MOVEMENT_MANUAL_ADJUSTMENT,
    MOVEMENT_OPENING_BALANCE,
    MOVEMENT_PURCHASE_RECEIPT,
    MOVEMENT_REVERSAL,
    MOVEMENT_SALES_DELIVERY,
    MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
    SALES_DELIVERY_CONFIRMATIONS,
    PhysicalStockConflictError,
    PhysicalStockRetryError,
    PhysicalStockUnavailableError,
    PhysicalStockValidationError,
    _as_physical_qty,
    _as_text,
    _check_sign,
    _completed_qty_for_line,
    _order_cancelled,
    _public_movement,
    _purchase_receipt_request,
    _qty_num,
    _resolve_delivery_location,
    _resolve_requested_delivery_lines,
    _same_adjustment,
    _same_delivery_confirmation,
    _same_payload,
    _sales_delivery_request,
    _stock_opname_adjustment_request,
    _validate_masters,
    build_stock_shortage_warning,
    opening_reference_id,
    now_iso,
)

SETTINGS_KEY = "warehouse_stock_standalone"
MODE_WAREHOUSE_STANDALONE = "warehouse_standalone"

_STATUS_PARTIAL = "PARTIAL"
_STATUS_CONFIRMED = "CONFIRMED"
_STATUS_FAILED = "FAILED"
_STATUS_POSTED = "POSTED"
_STATUS_SKIPPED = "SKIPPED"

# Active claim must not be cleared by a concurrent recover until the lease expires.
CLAIM_LEASE_SECONDS = 45

_fail_after_ledger = False
_fail_after_claim = False


def set_fail_after_ledger(flag: bool) -> None:
    global _fail_after_ledger
    _fail_after_ledger = bool(flag)


def set_fail_after_claim(flag: bool) -> None:
    global _fail_after_claim
    _fail_after_claim = bool(flag)


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


def _parse_iso(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _claim_is_stale(pending: dict, *, now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(timezone.utc)
    expires = _parse_iso(pending.get("lease_expires_at"))
    if expires is not None:
        return now >= expires
    claimed = _parse_iso(pending.get("claimed_at"))
    if claimed is None:
        return True
    return now >= claimed + timedelta(seconds=CLAIM_LEASE_SECONDS)


async def _existing_by_key(db, idempotency_key: str) -> Optional[dict]:
    return await db[LEDGER_COLLECTION].find_one(
        {"idempotency_key": idempotency_key},
        {"_id": 0},
    )


async def _finalize_from_pending(
    db,
    identity: dict,
    pending: dict,
    movement: dict,
) -> dict:
    """Apply qty from a committed ledger while pending_event still holds the claim."""
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
        return await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0}) or {}
    return updated


async def ensure_movement_finalized(db, movement: dict) -> dict:
    """If ledger exists but balance still has matching pending, finish finalize."""
    identity = {
        "master_item_id": str(movement.get("master_item_id") or ""),
        "branch_id": str(movement.get("branch_id") or ""),
        "warehouse_id": str(movement.get("warehouse_id") or ""),
    }
    if not all(identity.values()):
        return movement
    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    pending = (bal or {}).get("pending_event")
    if not isinstance(pending, dict):
        return movement
    key = str(pending.get("idempotency_key") or "")
    if key and key == str(movement.get("idempotency_key") or ""):
        await _finalize_from_pending(db, identity, pending, movement)
    return movement


async def recover_pending_balance(
    db,
    identity: dict,
    *,
    for_idempotency_key: Optional[str] = None,
) -> Optional[dict]:
    """Recover interrupted pending_event.

    - Ledger present → always finalize (never return success with stale balance).
    - No ledger + active (non-stale) claim for a *different* key → refuse (retry).
    - No ledger + stale lease → release claim.
    - No ledger + same for_idempotency_key as holder → release so the owner can retry.
    """
    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    if not bal:
        return None
    pending = bal.get("pending_event")
    if not isinstance(pending, dict):
        return bal
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
        return await _finalize_from_pending(db, identity, pending, existing)

    same_owner = bool(for_idempotency_key) and for_idempotency_key == key
    if same_owner or _claim_is_stale(pending):
        await db[BALANCE_COLLECTION].update_one(
            {**identity, "pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
        return await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})

    raise PhysicalStockRetryError(
        "Saldo fisik sedang diklaim proses lain (lease masih aktif)"
    )


def _new_claim(
    *,
    idempotency_key: str,
    movement_id: str,
    qty_delta: int,
    movement_type: str,
    actor_id: str,
    created_at: str,
    extra: Optional[dict] = None,
) -> dict:
    now = datetime.now(timezone.utc)
    claim = {
        "event_id": f"standalone:{idempotency_key}",
        "idempotency_key": idempotency_key,
        "movement_id": movement_id,
        "qty_delta": qty_delta,
        "movement_type": movement_type,
        "stage": "CLAIMED",
        "claimed_at": created_at or now.isoformat(),
        "lease_expires_at": (now + timedelta(seconds=CLAIM_LEASE_SECONDS)).isoformat(),
        "actor_id": actor_id,
        "fence_token": str(uuid.uuid4()),
    }
    if extra:
        claim.update(extra)
    return claim


async def apply_standalone_movement(db, raw: dict, *, actor_id: str) -> dict:
    """Post one warehouse movement without a multi-document transaction."""
    movement_type = _as_text((raw or {}).get("movement_type"), "movement_type")
    qty_delta = int((raw or {}).get("qty_delta") or 0)
    if movement_type == MOVEMENT_OPENING_BALANCE:
        if qty_delta < 0:
            raise PhysicalStockValidationError("Saldo awal tidak boleh negatif")
    elif movement_type in (
        MOVEMENT_MANUAL_ADJUSTMENT,
        MOVEMENT_STOCK_OPNAME_ADJUSTMENT,
        MOVEMENT_REVERSAL,
    ):
        pass
    else:
        _check_sign(movement_type, qty_delta)
        if qty_delta == 0:
            raise PhysicalStockValidationError("qty_delta harus bilangan bulat selain nol")

    idempotency_key = _as_text((raw or {}).get("idempotency_key"), "idempotency_key")
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
        requested["metadata"] = dict(metadata)

    existing = await _existing_by_key(db, idempotency_key)
    if existing:
        if not _same_payload(existing, requested):
            raise PhysicalStockConflictError(
                "Idempotency key sudah digunakan dengan payload yang berbeda"
            )
        await ensure_movement_finalized(db, existing)
        return _public_movement(existing)

    await _validate_masters(
        db,
        master_item_id=requested["master_item_id"],
        branch_id=requested["branch_id"],
        warehouse_id=requested["warehouse_id"],
        session=None,
    )

    identity = _identity(requested)
    await recover_pending_balance(db, identity, for_idempotency_key=idempotency_key)

    if movement_type == MOVEMENT_OPENING_BALANCE:
        prior_ledger = await db[LEDGER_COLLECTION].find_one(identity, {"_id": 1})
        if prior_ledger:
            raise PhysicalStockConflictError(
                "Saldo awal ditolak karena identitas ini sudah memiliki mutasi stok fisik"
            )
        prior_balance = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
        if prior_balance and prior_balance.get("pending_event") is None:
            if int(prior_balance.get("physical_qty") or 0) != 0 or prior_balance.get(
                "baseline_status"
            ) == BASELINE_RECONCILED:
                raise PhysicalStockConflictError(
                    "Saldo awal ditolak karena saldo fisik sudah ada"
                )

    existing = await _existing_by_key(db, idempotency_key)
    if existing:
        if not _same_payload(existing, requested):
            raise PhysicalStockConflictError(
                "Idempotency key sudah digunakan dengan payload yang berbeda"
            )
        await ensure_movement_finalized(db, existing)
        return _public_movement(existing)

    # Optional expected-qty fence for concurrent adjustments.
    expected_qty = (raw or {}).get("_expected_physical_qty")
    expected_version = (raw or {}).get("_expected_version")

    movement_id = str(uuid.uuid4())
    claim = _new_claim(
        idempotency_key=idempotency_key,
        movement_id=movement_id,
        qty_delta=qty_delta,
        movement_type=movement_type,
        actor_id=requested["actor_id"],
        created_at=created_at,
    )
    event_id = claim["event_id"]

    bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
    if bal and bal.get("pending_event"):
        await recover_pending_balance(db, identity, for_idempotency_key=idempotency_key)
        bal = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
        if bal and bal.get("pending_event"):
            raise PhysicalStockRetryError("Saldo fisik sedang diklaim proses lain")

    if bal is None:
        if expected_qty is not None and int(expected_qty) != 0:
            raise PhysicalStockRetryError("Saldo fisik berubah sebelum klaim")
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
        except DuplicateKeyError as exc:
            raise PhysicalStockRetryError("Saldo fisik dibuat bersamaan — coba lagi") from exc
        balance_before = 0
    else:
        claim_filter: Dict[str, Any] = {
            **identity,
            "$or": [
                {"pending_event": None},
                {"pending_event": {"$exists": False}},
            ],
        }
        if expected_qty is not None:
            claim_filter["physical_qty"] = int(expected_qty)
        if expected_version is not None:
            claim_filter["version"] = int(expected_version)
        claimed = await db[BALANCE_COLLECTION].find_one_and_update(
            claim_filter,
            {"$set": {"pending_event": claim, "updated_at": created_at}},
            return_document=ReturnDocument.BEFORE,
        )
        if claimed is None:
            raise PhysicalStockRetryError(
                "Saldo fisik berubah atau sedang diklaim — coba lagi"
            )
        balance_before = int(claimed.get("physical_qty") or 0)

    if _fail_after_claim:
        raise PhysicalStockUnavailableError("forced standalone abort after claim")

    # Recompute delta for target-style callers that pass expected qty.
    target_qty = (requested.get("metadata") or {}).get("target_physical_qty")
    if (
        movement_type == MOVEMENT_MANUAL_ADJUSTMENT
        and target_qty is not None
        and (raw or {}).get("_recompute_delta_from_claim")
    ):
        qty_delta = int(target_qty) - balance_before
        requested["qty_delta"] = qty_delta
        claim["qty_delta"] = qty_delta
        await db[BALANCE_COLLECTION].update_one(
            {**identity, "pending_event.event_id": event_id},
            {"$set": {"pending_event.qty_delta": qty_delta}},
        )

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
        "fence_token": claim.get("fence_token"),
    }
    if meta:
        doc["metadata"] = meta

    try:
        await db[LEDGER_COLLECTION].insert_one(doc)
    except DuplicateKeyError:
        raced = await _existing_by_key(db, idempotency_key)
        if raced and _same_payload(raced, requested):
            await ensure_movement_finalized(db, raced)
            # Drop our claim if still held and ledger belongs to the winner.
            await db[BALANCE_COLLECTION].update_one(
                {
                    **identity,
                    "pending_event.event_id": event_id,
                    "pending_event.fence_token": claim.get("fence_token"),
                },
                {"$unset": {"pending_event": ""}},
            )
            # Winner's pending may still need finalize if race left it.
            await ensure_movement_finalized(db, raced)
            return _public_movement(raced)
        await db[BALANCE_COLLECTION].update_one(
            {
                **identity,
                "pending_event.event_id": event_id,
                "pending_event.fence_token": claim.get("fence_token"),
            },
            {"$unset": {"pending_event": ""}},
        )
        raise PhysicalStockConflictError(
            "Idempotency key sudah digunakan dengan payload yang berbeda"
        )

    if _fail_after_ledger:
        raise PhysicalStockUnavailableError("forced standalone abort after ledger")

    set_fields = {
        "last_movement_id": movement_id,
        "updated_at": created_at,
    }
    if establishes:
        set_fields["baseline_status"] = BASELINE_RECONCILED

    finalized = await db[BALANCE_COLLECTION].find_one_and_update(
        {
            **identity,
            "pending_event.event_id": event_id,
            "pending_event.fence_token": claim.get("fence_token"),
        },
        {
            "$inc": {"physical_qty": qty_delta, "version": 1},
            "$set": set_fields,
            "$unset": {"pending_event": ""},
        },
        return_document=ReturnDocument.AFTER,
    )
    if finalized is None:
        await ensure_movement_finalized(db, doc)
        bal_now = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0}) or {}
        if bal_now.get("pending_event"):
            pending = bal_now["pending_event"]
            if str(pending.get("idempotency_key") or "") == idempotency_key:
                await _finalize_from_pending(db, identity, pending, doc)
    return _public_movement(doc)


async def post_opening_balance_standalone(db, item_id: str, raw: dict, *, actor_id: str) -> dict:
    payload = raw or {}
    branch_id = _as_text(payload.get("branch_id"), "branch_id")
    warehouse_id = _as_text(payload.get("warehouse_id"), "warehouse_id")
    physical_qty = _as_physical_qty(payload.get("physical_qty"))
    reason = str(payload.get("reason") or "").strip() or "Saldo awal"
    last_error: Optional[BaseException] = None
    for _ in range(5):
        try:
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
        except PhysicalStockRetryError as exc:
            last_error = exc
            continue
    raise PhysicalStockUnavailableError(
        f"Saldo awal gagal setelah retry: {last_error}"
    )


async def adjust_to_target_standalone(db, item_id: str, raw: dict, *, actor_id: str) -> dict:
    """Set warehouse book qty to target using claim-time balance (not a stale snapshot)."""
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
        await ensure_movement_finalized(db, existing)
        return {
            "master_item_id": master_item_id,
            "branch_id": branch_id,
            "warehouse_id": warehouse_id,
            "previous_physical_qty": int(existing.get("balance_before") or 0),
            "target_physical_qty": target,
            "qty_delta": int(existing.get("qty_delta") or 0),
            "physical_qty": int(
                existing.get("balance_after")
                if existing.get("balance_after") is not None
                else target
            ),
            "movement_id": existing.get("id"),
            "changed": True,
            "engine": MODE_WAREHOUSE_STANDALONE,
        }

    identity = {
        "master_item_id": master_item_id,
        "branch_id": branch_id,
        "warehouse_id": warehouse_id,
    }
    last_error: Optional[BaseException] = None
    for _ in range(8):
        try:
            await recover_pending_balance(
                db, identity, for_idempotency_key=idempotency_key,
            )
            current_doc = await db[BALANCE_COLLECTION].find_one(identity, {"_id": 0})
            if current_doc and current_doc.get("pending_event"):
                raise PhysicalStockRetryError("Saldo fisik sedang diklaim")
            current = int((current_doc or {}).get("physical_qty") or 0)
            version = int((current_doc or {}).get("version") or 0) if current_doc else None
            if current == target and current_doc is not None:
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
            delta = target - current
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
                    "metadata": {
                        "target_physical_qty": target,
                        "baseline_status": BASELINE_RECONCILED,
                    },
                    "_expected_physical_qty": current,
                    "_expected_version": version,
                    "_recompute_delta_from_claim": True,
                },
                actor_id=actor_id,
            )
            # Guard: claimed delta must land on target.
            if int(movement.get("balance_after") or 0) != target:
                raise PhysicalStockRetryError(
                    "Hasil adjustment tidak mencapai target — retry"
                )
            return {
                "master_item_id": master_item_id,
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
                "previous_physical_qty": int(movement.get("balance_before") or 0),
                "target_physical_qty": target,
                "qty_delta": int(movement.get("qty_delta") or 0),
                "physical_qty": int(movement.get("balance_after") or target),
                "movement_id": movement.get("id"),
                "changed": True,
                "engine": MODE_WAREHOUSE_STANDALONE,
            }
        except PhysicalStockRetryError as exc:
            last_error = exc
            continue
    raise PhysicalStockConflictError(
        f"Adjustment gagal karena perubahan saldo bersamaan: {last_error}"
    )


async def apply_purchase_receipt_lines_standalone(
    db, receipt: dict, *, actor_id: str
) -> Dict[str, Any]:
    """Post each GR line idempotently. Returns COMPLETE or PARTIAL summary."""
    from services.physical_stock import receipt_line_posts_physical_stock

    lines = receipt.get("lines") if isinstance(receipt.get("lines"), list) else []
    if not lines:
        raise PhysicalStockValidationError("Minimal satu baris penerimaan")
    movements = []
    failures = []
    skipped = 0
    for line in lines:
        if not await receipt_line_posts_physical_stock(db, line):
            skipped += 1
            continue
        req = _purchase_receipt_request(receipt, line, actor_id=actor_id)
        try:
            mov = await apply_standalone_movement(db, req, actor_id=actor_id)
            movements.append(mov)
        except Exception as exc:  # noqa: BLE001
            failures.append({
                "line_id": str(line.get("line_id") or ""),
                "error": str(exc),
            })
    # All-SERVICE receipt: no physical lines to post → COMPLETE with zero movements.
    status = "COMPLETE" if not failures else ("PARTIAL" if movements else "FAILED")
    return {
        "status": status,
        "movements": movements,
        "failures": failures,
        "posted_count": len(movements),
        "failed_count": len(failures),
        "skipped_non_inventory": skipped,
        "engine": MODE_WAREHOUSE_STANDALONE,
    }


async def ensure_receipt_movements_complete(db, receipt: dict, *, actor_id: str) -> Dict[str, Any]:
    """Retry path: receipt may exist while some line ledgers are missing."""
    outcome = await apply_purchase_receipt_lines_standalone(db, receipt, actor_id=actor_id)
    posting = {
        "status": outcome["status"],
        "posted_count": outcome["posted_count"],
        "failed_count": outcome["failed_count"],
        "updated_at": now_iso(),
        "engine": MODE_WAREHOUSE_STANDALONE,
    }
    await db["purchase_goods_receipts"].update_one(
        {"id": receipt["id"]},
        {"$set": {"stock_posting": posting}},
    )
    return outcome


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
    order_id: str = "",
    line_id: str = "",
    sales_delivery_id: str = "",
) -> dict:
    if not isinstance(qty, int) or qty <= 0:
        raise PhysicalStockValidationError("Qty pengiriman harus bilangan bulat > 0")
    line = {
        "line_idx": int(line_idx),
        "qty": int(qty),
        "master_item_id": master_item_id,
        "line_id": line_id or f"idx:{line_idx}",
    }
    req = _sales_delivery_request(
        line=line,
        order_id=order_id or "unknown",
        confirmation_id=confirmation_id,
        branch_id=branch_id,
        warehouse_id=warehouse_id,
        actor_id=actor_id,
        reason=reason,
        sales_delivery_id=sales_delivery_id,
    )
    return await apply_standalone_movement(db, req, actor_id=actor_id)


async def apply_confirmed_sales_delivery_standalone(
    db,
    *,
    order: dict,
    confirmation_id: str,
    lines: List[dict],
    actor_id: str,
    warehouse_id: Optional[str] = None,
    branch_id: Optional[str] = None,
    reason: str = "",
    sales_delivery_id: Optional[str] = None,
) -> dict:
    """HTTP Konfirmasi Terkirim stock path for standalone (no multi-doc txn).

    Order confirmation is already persisted by the caller. This posts per-line
    SALES_DELIVERY movements idempotently and records physical_sales_delivery_confirmations.
    Shortage only warns — never blocks (aligned with #585).
    """
    if not isinstance(order, dict) or not str(order.get("id") or "").strip():
        raise PhysicalStockValidationError("Pesanan tidak ditemukan")
    if _order_cancelled(order):
        raise PhysicalStockValidationError("Pesanan sudah dibatalkan")

    order_id = str(order["id"])
    confirmation_id = _as_text(confirmation_id, "confirmation_id")
    actor = _as_text(actor_id, "actor_id")
    note = str(reason or "").strip() or "Konfirmasi terkirim"
    sj_id = str(sales_delivery_id or "").strip()
    resolved = _resolve_requested_delivery_lines(order, lines)
    resolved_branch, resolved_warehouse = await _resolve_delivery_location(
        db, None, branch_id=branch_id, warehouse_id=warehouse_id,
    )
    spec = {
        "order_id": order_id,
        "branch_id": resolved_branch,
        "warehouse_id": resolved_warehouse,
        "lines": resolved,
    }
    existing = await db[SALES_DELIVERY_CONFIRMATIONS].find_one(
        {"id": confirmation_id}, {"_id": 0},
    )
    if existing:
        if not _same_delivery_confirmation(existing, spec):
            raise PhysicalStockConflictError(
                "Konfirmasi terkirim sudah digunakan dengan payload yang berbeda"
            )
        movements = []
        failures = []
        for line in resolved:
            key = (
                f"sales-delivery:{confirmation_id}:{line['line_idx']}:"
                f"{line['master_item_id']}"
            )
            try:
                mov = await _existing_by_key(db, key)
                if not mov:
                    # Resume missing movements on retry.
                    mov = await apply_standalone_movement(
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
                        actor_id=actor,
                    )
                else:
                    await ensure_movement_finalized(db, mov)
                movements.append(_public_movement(mov))
            except Exception as exc:  # noqa: BLE001
                failures.append({"line_idx": line["line_idx"], "error": str(exc)})
        posting_status = (
            "COMPLETE" if not failures else ("PARTIAL" if movements else "FAILED")
        )
        await db[SALES_DELIVERY_CONFIRMATIONS].update_one(
            {"id": confirmation_id},
            {"$set": {
                "stock_posting": {
                    "status": posting_status,
                    "posted_count": len(movements),
                    "failed_count": len(failures),
                    "failures": failures,
                    "engine": MODE_WAREHOUSE_STANDALONE,
                    "updated_at": now_iso(),
                },
            }},
        )
        return {
            "confirmation_id": confirmation_id,
            "order_id": order_id,
            "branch_id": resolved_branch,
            "warehouse_id": resolved_warehouse,
            "replayed": True,
            "movements": movements,
            "stock_warnings": list(existing.get("stock_warnings") or []),
            "stock_posting": {
                "status": posting_status,
                "posted_count": len(movements),
                "failed_count": len(failures),
            },
            "engine": MODE_WAREHOUSE_STANDALONE,
        }

    from services.stock_compatibility_read import proven_delivered_qty_by_order_line

    # Compatibility-proven *prior* delivery only. The business confirm is often
    # stamped on order.shipments / delivery_confirmations before the warehouse
    # ledger exists (crash/retry). Exclude this confirmation + SJ so ready-qty
    # does not treat the in-flight confirm as already delivered history.
    exclude_conf_ids = [confirmation_id]
    exclude_ship_ids = [sj_id] if sj_id else []
    proven_by_line, _unproven = proven_delivered_qty_by_order_line(
        order,
        exclude_confirmation_ids=exclude_conf_ids,
        exclude_shipment_ids=exclude_ship_ids,
    )
    for line in resolved:
        completed = _completed_qty_for_line(order, line["line_idx"])
        line_idx = int(line["line_idx"])
        proven_done = int(max(_qty_num(proven_by_line.get(line_idx, 0)), 0))
        # Prior ledger for this line — omit movements for the current confirmation
        # (idempotent retry / partial resume must not count itself as history).
        prior = 0
        async for mov in db[LEDGER_COLLECTION].find(
            {
                "movement_type": MOVEMENT_SALES_DELIVERY,
                "metadata.order_id": order_id,
                "metadata.line_idx": line_idx,
            },
            {"_id": 0, "qty_delta": 1, "metadata.confirmation_id": 1},
        ):
            mov_conf = str((mov.get("metadata") or {}).get("confirmation_id") or "").strip()
            if mov_conf and mov_conf == confirmation_id:
                continue
            prior += abs(int(mov.get("qty_delta") or 0))
        async for mov in db[LEDGER_COLLECTION].find(
            {
                "movement_type": MOVEMENT_REVERSAL,
                "metadata.order_id": order_id,
                "metadata.line_idx": line_idx,
            },
            {"_id": 0, "qty_delta": 1, "metadata.confirmation_id": 1},
        ):
            mov_conf = str((mov.get("metadata") or {}).get("confirmation_id") or "").strip()
            if mov_conf and mov_conf == confirmation_id:
                continue
            prior -= abs(int(mov.get("qty_delta") or 0))
        already = max(proven_done, max(int(prior), 0))
        ready = max(int(completed) - already, 0)
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
    failures = []
    for line in resolved:
        try:
            mov = await apply_standalone_movement(
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
                actor_id=actor,
            )
            movements.append(mov)
        except Exception as exc:  # noqa: BLE001
            failures.append({"line_idx": line["line_idx"], "error": str(exc)})

    posting_status = "COMPLETE" if not failures else ("PARTIAL" if movements else "FAILED")
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
        "engine": MODE_WAREHOUSE_STANDALONE,
        "stock_posting": {
            "status": posting_status,
            "failed_count": len(failures),
            "posted_count": len(movements),
            "failures": failures,
            "updated_at": now_iso(),
        },
    }
    try:
        await db[SALES_DELIVERY_CONFIRMATIONS].insert_one(confirmation)
    except DuplicateKeyError:
        return await apply_confirmed_sales_delivery_standalone(
            db,
            order=order,
            confirmation_id=confirmation_id,
            lines=lines,
            actor_id=actor_id,
            warehouse_id=warehouse_id,
            branch_id=branch_id,
            reason=reason,
            sales_delivery_id=sales_delivery_id,
        )

    if failures and not movements:
        raise PhysicalStockUnavailableError(
            f"Gagal memposting stok pengiriman: {failures[0].get('error')}"
        )
    return {
        "confirmation_id": confirmation_id,
        "order_id": order_id,
        "branch_id": resolved_branch,
        "warehouse_id": resolved_warehouse,
        "replayed": False,
        "movements": movements,
        "stock_warnings": stock_warnings,
        "stock_posting": confirmation["stock_posting"],
        "engine": MODE_WAREHOUSE_STANDALONE,
    }


async def reverse_confirmed_sales_delivery_standalone(
    db,
    *,
    confirmation_id: str,
    actor_id: str,
    reason: str = "Pembatalan konfirmasi terkirim",
) -> dict:
    """Reverse standalone delivery movements one-by-one (idempotent)."""
    from services.physical_stock import PhysicalStockNotFoundError

    confirmation_id = _as_text(confirmation_id, "confirmation_id")
    doc = await db[SALES_DELIVERY_CONFIRMATIONS].find_one(
        {"id": confirmation_id}, {"_id": 0},
    )
    if not doc:
        raise PhysicalStockNotFoundError("Konfirmasi terkirim tidak ditemukan")
    if doc.get("reversed"):
        return {
            "confirmation_id": confirmation_id,
            "replayed": True,
            "reversed": True,
            "movements": [],
            "reversals": list(doc.get("reversal_movement_ids") or []),
        }

    originals = await db[LEDGER_COLLECTION].find(
        {
            "movement_type": MOVEMENT_SALES_DELIVERY,
            "metadata.confirmation_id": confirmation_id,
        },
        {"_id": 0},
    ).to_list(500)
    movements = []
    reversal_ids = []
    for original in originals:
        key = f"sales-delivery-reversal:{confirmation_id}:{original['id']}"
        existing = await _existing_by_key(db, key)
        if existing:
            await ensure_movement_finalized(db, existing)
            movements.append(_public_movement(existing))
            reversal_ids.append(existing["id"])
            continue
        delta = -int(original.get("qty_delta") or 0)
        mov = await apply_standalone_movement(
            db,
            {
                "master_item_id": original["master_item_id"],
                "branch_id": original["branch_id"],
                "warehouse_id": original["warehouse_id"],
                "movement_type": MOVEMENT_REVERSAL,
                "qty_delta": delta,
                "reference_type": LEDGER_COLLECTION,
                "reference_id": original["id"],
                "reason": reason,
                "idempotency_key": key,
                "metadata": {
                    "confirmation_id": confirmation_id,
                    "order_id": (original.get("metadata") or {}).get("order_id"),
                    "line_idx": (original.get("metadata") or {}).get("line_idx"),
                    "reversed_movement_id": original["id"],
                },
            },
            actor_id=actor_id,
        )
        movements.append(mov)
        reversal_ids.append(mov["id"])

    await db[SALES_DELIVERY_CONFIRMATIONS].update_one(
        {"id": confirmation_id, "reversed": {"$ne": True}},
        {"$set": {
            "reversed": True,
            "reversed_at": now_iso(),
            "reversed_by": actor_id,
            "reversal_reason": reason,
            "reversal_movement_ids": reversal_ids,
        }},
    )
    return {
        "confirmation_id": confirmation_id,
        "replayed": False,
        "reversed": True,
        "movements": movements,
        "reversals": reversal_ids,
        "engine": MODE_WAREHOUSE_STANDALONE,
    }


async def cancel_sales_delivery_document_standalone(
    db,
    delivery: dict,
    *,
    actor_id: str,
    reason: str = "Pembatalan Surat Jalan",
) -> dict:
    """Soft-cancel SJ + reverse standalone confirmations without multi-doc txn."""
    from services.physical_stock import (
        PhysicalStockNotFoundError,
        _r2_confirmation_ids_for_sales_delivery,
        _recompute_order_delivery_after_cancel,
        _sales_delivery_identity_tokens,
        _shipment_matches_delivery,
    )

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
    if fresh.get("is_cancelled") is True or str(fresh.get("status") or "").lower() in (
        "cancelled", "dibatalkan", "deleted",
    ):
        order = await db.orders.find_one({"id": po_id}, {"_id": 0}) if po_id else None
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
            "engine": MODE_WAREHOUSE_STANDALONE,
        }

    confirmation_ids = await _r2_confirmation_ids_for_sales_delivery(db, fresh)
    reversal_results = []
    all_reversal_ids: List[str] = []
    for cid in confirmation_ids:
        result = await reverse_confirmed_sales_delivery_standalone(
            db, confirmation_id=cid, actor_id=actor, reason=note,
        )
        reversal_results.append(result)
        all_reversal_ids.extend(list(result.get("reversals") or []))

    cancelled_at = now_iso()
    await db.sales_deliveries.update_one(
        {"id": delivery_id, "is_cancelled": {"$ne": True}},
        {"$set": {
            "is_cancelled": True,
            "cancelled": True,
            "status": "DIBATALKAN",
            "cancelled_at": cancelled_at,
            "cancel_reason": note,
            "r2_reversal": {
                "reversed_confirmations": reversal_results,
                "confirmation_ids": confirmation_ids,
                "engine": MODE_WAREHOUSE_STANDALONE,
            },
            "updated_at": cancelled_at,
        }},
    )

    order_patch: Dict[str, Any] = {}
    if po_id:
        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
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
            order["shipments"] = shipments
            order["delivery_confirmations"] = confirmations
            recomputed = _recompute_order_delivery_after_cancel(order)
            order_patch = {
                "shipments": shipments,
                "delivery_confirmations": confirmations,
                "qty_delivered": recomputed["qty_delivered"],
                "delivery_status": recomputed["delivery_status"],
                "current_stage": recomputed["current_stage"],
                "updated_at": cancelled_at,
            }
            await db.orders.update_one({"id": po_id}, {"$set": order_patch})

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
        "engine": MODE_WAREHOUSE_STANDALONE,
    }


async def cancel_purchase_receipt_standalone(
    db,
    receipt: dict,
    *,
    actor_id: str,
    reason: str = "Pembatalan penerimaan barang",
) -> Dict[str, Any]:
    """Reverse standalone purchase-receipt movements without multi-doc transactions.

    Idempotent per original movement via purchase-receipt-reversal keys. Does not
    open a Mongo transaction; caller owns receipt cancellation field updates.
    """
    receipt_id = _as_text((receipt or {}).get("id"), "receipt_id")
    note = str(reason or "").strip() or "Pembatalan penerimaan barang"
    originals = await db[LEDGER_COLLECTION].find(
        {
            "movement_type": MOVEMENT_PURCHASE_RECEIPT,
            "metadata.receipt_id": receipt_id,
        },
        {"_id": 0},
    ).to_list(500)
    if not originals:
        raise PhysicalStockValidationError(
            "Penerimaan tidak memiliki movement stok fisik yang dapat dibatalkan."
        )
    reversal_ids: List[str] = []
    movements: List[dict] = []
    for original in originals:
        key = f"purchase-receipt-reversal:{receipt_id}:{original['id']}"
        existing = await _existing_by_key(db, key)
        if existing:
            await ensure_movement_finalized(db, existing)
            movements.append(_public_movement(existing))
            reversal_ids.append(existing["id"])
            continue
        delta = -int(original.get("qty_delta") or 0)
        mov = await apply_standalone_movement(
            db,
            {
                "master_item_id": original["master_item_id"],
                "branch_id": original["branch_id"],
                "warehouse_id": original["warehouse_id"],
                "movement_type": MOVEMENT_REVERSAL,
                "qty_delta": delta,
                "reference_type": LEDGER_COLLECTION,
                "reference_id": original["id"],
                "reason": note,
                "idempotency_key": key,
                "metadata": {
                    "receipt_id": receipt_id,
                    "reversed_movement_id": original["id"],
                    "reverses_movement_id": original["id"],
                },
            },
            actor_id=actor_id,
        )
        movements.append(mov)
        reversal_ids.append(mov["id"])
    return {
        "receipt_id": receipt_id,
        "reversals": reversal_ids,
        "movements": movements,
        "engine": MODE_WAREHOUSE_STANDALONE,
    }


async def apply_opname_lines_standalone(
    db,
    session_doc: dict,
    lines: List[dict],
    *,
    actor_id: str,
    occurred_at: Optional[str] = None,
    resume: bool = False,
) -> Dict[str, Any]:
    """Post opname lines one-by-one. POSTED lines are never re-posted (idempotent)."""
    stamp = occurred_at or now_iso()
    results = []
    for line in lines:
        line_id = str(line.get("line_id") or "")
        prior_status = str(line.get("post_status") or "").strip().upper()
        if resume and prior_status == _STATUS_POSTED:
            results.append({
                "line_id": line_id,
                "status": _STATUS_POSTED,
                "movement": {"id": line.get("movement_id")},
                "skipped_resume": True,
            })
            continue
        diff = int(line.get("difference_qty") or 0)
        if diff == 0:
            results.append({"line_id": line_id, "status": _STATUS_SKIPPED, "movement": None})
            continue
        req = _stock_opname_adjustment_request(
            session_doc, line, actor_id=actor_id, occurred_at=stamp,
        )
        try:
            mov = await apply_standalone_movement(db, req, actor_id=actor_id)
            results.append({"line_id": line_id, "status": _STATUS_POSTED, "movement": mov})
        except Exception as exc:  # noqa: BLE001
            results.append({
                "line_id": line_id,
                "status": _STATUS_FAILED,
                "error": str(exc),
            })

    posted = sum(1 for row in results if row.get("status") == _STATUS_POSTED)
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
