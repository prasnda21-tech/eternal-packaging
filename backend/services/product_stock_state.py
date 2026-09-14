"""Authoritative Physical Stock State Service (stored in product_stock_state).

Maintains the single authoritative physical inventory state per exact master_item_id.
Business Invariants:
1. TOTAL = UNPRINTED + PRINTED (all stored in canonical Base UOM in the SAME document)
2. Opening balance must be explicitly initialized (one-time; second initialization rejected with HTTP 409)
3. Opening balance records exactly one immutable ledger event: OPENING_BALANCE
4. Zero automated stock mutation until physical_stock.enabled is explicitly enabled by admin
5. Does NOT derive from or alter current_stock.py
"""
from __future__ import annotations

import asyncio
import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.product_unit_conversion import get_product_unit_setting, normalize_uom
from services.stock_ledger import (
    record_stock_movement,
    MOVEMENT_SALDO_AWAL,
    MOVEMENT_KOREKSI,
    safe_float,
)

COLLECTION = "product_stock_state"
STATUS_INITIALIZING = "INITIALIZING"
STATUS_INITIALIZED = "INITIALIZED"
HOLDING_TEAM_ROLE = "holding_team"
HOLDING_TEAM_ONLY_MSG = "Hanya HOLDING_TEAM yang dapat mengkoreksi saldo awal stok fisik"

_init_lock = asyncio.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def validate_non_negative_number(v: Any, field_name: str) -> float:
    if v is None or (isinstance(v, str) and not str(v).strip()):
        raise HTTPException(400, f"Field '{field_name}' wajib diisi")
    if isinstance(v, bool):
        raise HTTPException(400, f"Field '{field_name}' harus berupa angka")
    try:
        n = float(v)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, f"Field '{field_name}' harus berupa angka") from e
    if math.isnan(n) or math.isinf(n):
        raise HTTPException(400, f"Field '{field_name}' tidak valid (NaN / Infinity)")
    if n < 0:
        raise HTTPException(400, f"Field '{field_name}' tidak boleh negatif")
    if n == int(n):
        return int(n)
    return round(n, 6)


async def get_product_stock_state(db, master_item_id: str) -> Optional[Dict[str, Any]]:
    """Pure read. Returns stock state document for exact master_item_id or None."""
    mid = str(master_item_id or "").strip()
    if not mid:
        return None
    return await db[COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]}, {"_id": 0})


async def initialize_opening_balance(
    db,
    master_item_id: str,
    payload: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Atomically initializes opening physical balance for an exact product.
    
    Guarantees:
    - Database-level durable concurrency: deterministic _id = master_item_id for state,
      and _id = f"opening_balance:{master_item_id}" for movement.
    - Does NOT rely on in-process locks for correctness across multiple processes/workers.
    - One-time only: if already initialized, raises HTTP 409 Conflict.
    - Invariant: TOTAL == UNPRINTED + PRINTED.
    - Stored in canonical Base UOM.
    - Records exactly one immutable OPENING_BALANCE ledger movement.
    - Recoverable 3-step state machine: INITIALIZING -> MOVEMENT -> INITIALIZED.
    - Crash recovery handles interrupted initializations safely.
    - Conflicting retries rejected with HTTP 409.
    - physical_stock.enabled is NOT altered.
    """
    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")

    # 1. Verify master item exists
    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master:
        master = await db.master_items.find_one({"accurate_item_id": mid}, {"_id": 0})
        if master:
            mid = master["id"]
        else:
            raise HTTPException(404, f"Produk dengan ID '{mid}' tidak ditemukan")

    aid = master.get("accurate_item_id")

    # 2. Canonical Base UOM verification
    unit_setting = await get_product_unit_setting(db, mid)
    canonical_base_uom = normalize_uom(
        (unit_setting or {}).get("base_uom") or master.get("unit") or "PCS"
    )

    req_uom = normalize_uom(payload.get("base_uom") or canonical_base_uom)
    if req_uom != canonical_base_uom:
        raise HTTPException(
            400,
            f"Satuan saldo awal ('{req_uom}') tidak sesuai dengan Satuan Dasar kanonikal ('{canonical_base_uom}')",
        )

    # 3. Quantity validation
    raw_unprinted = payload.get("unprinted_qty")
    raw_printed = payload.get("printed_qty")
    raw_total = payload.get("total_qty")

    unprinted_qty = validate_non_negative_number(raw_unprinted, "unprinted_qty")
    printed_qty = validate_non_negative_number(raw_printed, "printed_qty")

    if raw_total is not None:
        total_qty = validate_non_negative_number(raw_total, "total_qty")
        if abs(total_qty - (unprinted_qty + printed_qty)) > 1e-6:
            raise HTTPException(
                400,
                f"Invarian tidak terpenuhi: Total ({total_qty}) harus sama dengan "
                f"Belum Dicetak ({unprinted_qty}) + Sudah Dicetak ({printed_qty})",
            )
    else:
        total_qty = unprinted_qty + printed_qty

    # 4. Actor details
    actor = None
    if isinstance(user, dict):
        actor = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": user.get("name") or user.get("email") or "User",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }

    stamp = now_iso()
    op_mov_id = f"opening_balance:{mid}"
    event_key = f"opening_balance:{mid}"

    async with _init_lock:
        was_newly_created = False
        # Step A: Check or atomically create state document in INITIALIZING status
        existing_state = await db[COLLECTION].find_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]}
        )

        if existing_state:
            # Already initialized?
            if existing_state.get("status") == STATUS_INITIALIZED or existing_state.get("initialized") is True:
                raise HTTPException(409, "Saldo awal stok fisik sudah ditetapkan")

            # In INITIALIZING status: check if retry has matching payload
            if (
                normalize_uom(existing_state.get("base_uom")) != canonical_base_uom
                or abs(safe_float(existing_state.get("unprinted_qty")) - unprinted_qty) > 1e-6
                or abs(safe_float(existing_state.get("printed_qty")) - printed_qty) > 1e-6
                or abs(safe_float(existing_state.get("total_qty")) - total_qty) > 1e-6
            ):
                raise HTTPException(
                    409,
                    "Inisialisasi saldo awal sedang berlangsung dengan nilai berbeda atau saldo awal sudah ditetapkan",
                )
        else:
            # Atomic insert of INITIALIZING state with deterministic _id
            init_doc = {
                "_id": mid,
                "master_item_id": mid,
                "accurate_item_id": aid,
                "base_uom": canonical_base_uom,
                "total_qty": total_qty,
                "unprinted_qty": unprinted_qty,
                "printed_qty": printed_qty,
                "status": STATUS_INITIALIZING,
                "initialized": False,
                "initialized_at": stamp,
                "initialized_by": actor,
                "updated_at": stamp,
                "version": 1,
                "opening_movement_id": None,
            }
            try:
                await db[COLLECTION].insert_one(init_doc)
                was_newly_created = True
            except DuplicateKeyError:
                # Concurrent writer created the record simultaneously
                existing_state = await db[COLLECTION].find_one(
                    {"$or": [{"_id": mid}, {"master_item_id": mid}]}
                )
                if not existing_state:
                    raise HTTPException(500, "Konflik konkurensi pada pembuatan status stok fisik")
                if existing_state.get("status") == STATUS_INITIALIZED or existing_state.get("initialized") is True:
                    raise HTTPException(409, "Saldo awal stok fisik sudah ditetapkan")
                if (
                    normalize_uom(existing_state.get("base_uom")) != canonical_base_uom
                    or abs(safe_float(existing_state.get("unprinted_qty")) - unprinted_qty) > 1e-6
                    or abs(safe_float(existing_state.get("printed_qty")) - printed_qty) > 1e-6
                    or abs(safe_float(existing_state.get("total_qty")) - total_qty) > 1e-6
                ):
                    raise HTTPException(
                        409,
                        "Inisialisasi saldo awal sedang berlangsung dengan nilai berbeda atau saldo awal sudah ditetapkan",
                    )

        # Step B: Ensure deterministic movement exists in stock_movements
        movement = await db.stock_movements.find_one(
            {"$or": [{"_id": op_mov_id}, {"id": op_mov_id}, {"event_key": event_key}]},
            {"_id": 0},
        )
        if not movement:
            try:
                movement = await record_stock_movement(
                    db,
                    movement_type=MOVEMENT_SALDO_AWAL,
                    master_item_id=mid,
                    accurate_item_id=aid,
                    transaction_qty=total_qty,
                    transaction_uom=canonical_base_uom,
                    source_entity="SALDO_AWAL",
                    destination_entity="GUDANG_UTAMA",
                    reference_number=f"SA-{master.get('item_code') or mid[:8]}",
                    description=f"Saldo Awal Stok Fisik ({unprinted_qty} Belum Dicetak, {printed_qty} Sudah Dicetak)",
                    delta_unprinted=unprinted_qty,
                    delta_printed=printed_qty,
                    event_key=event_key,
                    user=actor,
                    now=stamp,
                    movement_id=op_mov_id,
                )
            except DuplicateKeyError:
                movement = await db.stock_movements.find_one(
                    {"$or": [{"_id": op_mov_id}, {"id": op_mov_id}]},
                    {"_id": 0},
                )
                if not movement:
                    raise HTTPException(500, "Gagal memverifikasi mutasi saldo awal di ledger")
            except Exception as ex:
                if was_newly_created:
                    try:
                        await db[COLLECTION].delete_one({"_id": mid, "status": STATUS_INITIALIZING})
                    except Exception:
                        pass
                raise HTTPException(500, f"Gagal mencatat mutasi saldo awal di ledger: {str(ex)}") from ex

        # Step C: Atomically finalize state to INITIALIZED
        mov_id = movement.get("id") or op_mov_id
        finalize_stamp = now_iso()
        finalize_update = {
            "status": STATUS_INITIALIZED,
            "initialized": True,
            "opening_movement_id": mov_id,
            "updated_at": finalize_stamp,
        }
        try:
            await db[COLLECTION].update_one(
                {"$or": [{"_id": mid}, {"master_item_id": mid}]},
                {"$set": finalize_update},
            )
        except Exception as ex:
            comp_error = None
            if mov_id:
                try:
                    await db.stock_movements.delete_one({"$or": [{"_id": mov_id}, {"id": mov_id}]})
                except Exception as cex:
                    comp_error = cex
            if was_newly_created:
                try:
                    await db[COLLECTION].delete_one({"_id": mid, "status": STATUS_INITIALIZING})
                except Exception:
                    pass
            if comp_error:
                raise HTTPException(
                    500,
                    f"KRITIKAL: Gagal menyimpan status stok fisik ({str(ex)}) dan rollback mutasi saldo awal gagal ({str(comp_error)}). ID mutasi: {mov_id}",
                ) from ex
            raise HTTPException(500, f"Gagal menyimpan status stok fisik: {str(ex)}") from ex

        saved_state = await db[COLLECTION].find_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]},
            {"_id": 0},
        )
        return {
            "stock_state": saved_state,
            "movement": movement,
            "message": "Saldo awal stok fisik berhasil ditetapkan",
        }


def _actor_from_user(user: Optional[Dict[str, Any]]) -> Optional[Dict[str, str]]:
    if not isinstance(user, dict):
        return None
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": user.get("name") or user.get("email") or "User",
        "email": user.get("email") or "",
        "role": user.get("role") or "",
    }


def require_holding_team(user: Optional[Dict[str, Any]]) -> None:
    role = str((user or {}).get("role") or "").strip().lower()
    if role != HOLDING_TEAM_ROLE:
        raise HTTPException(403, HOLDING_TEAM_ONLY_MSG)


async def correct_opening_balance(
    db,
    master_item_id: str,
    payload: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """HOLDING_TEAM-only append-only correction of current physical buckets.

    Does NOT delete/overwrite SALDO_AWAL. Does NOT insert a second SALDO_AWAL.
    Does NOT reset initialized. Does NOT change physical_stock settings.
    Target is current physical qty (unprinted + printed), not a history replay.
    """
    require_holding_team(user)

    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")

    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master:
        master = await db.master_items.find_one({"accurate_item_id": mid}, {"_id": 0})
        if master:
            mid = master["id"]
        else:
            raise HTTPException(404, f"Produk dengan ID '{mid}' tidak ditemukan")

    state = await get_product_stock_state(db, mid)
    if not state or not state.get("initialized") or state.get("status") != STATUS_INITIALIZED:
        raise HTTPException(400, "Saldo awal stok fisik produk belum ditetapkan")

    if state.get("pending_event"):
        raise HTTPException(409, "Ada mutasi stok yang sedang berjalan untuk item ini")

    note = str((payload or {}).get("note") or (payload or {}).get("reason") or "").strip()
    if not note:
        raise HTTPException(400, "Alasan koreksi wajib diisi")

    new_unp = validate_non_negative_number((payload or {}).get("unprinted_qty"), "unprinted_qty")
    new_pri = validate_non_negative_number((payload or {}).get("printed_qty"), "printed_qty")
    raw_total = (payload or {}).get("total_qty")
    new_tot = new_unp + new_pri
    if raw_total is not None:
        declared = validate_non_negative_number(raw_total, "total_qty")
        if abs(declared - new_tot) > 1e-6:
            raise HTTPException(
                400,
                f"Invarian tidak terpenuhi: Total ({declared}) harus sama dengan "
                f"Belum Dicetak ({new_unp}) + Sudah Dicetak ({new_pri})",
            )

    old_unp = safe_float(state.get("unprinted_qty"))
    old_pri = safe_float(state.get("printed_qty"))
    old_tot = safe_float(state.get("total_qty"))
    d_unp = new_unp - old_unp
    d_pri = new_pri - old_pri
    d_tot = new_tot - old_tot

    client_request_id = str((payload or {}).get("client_request_id") or "").strip() or str(uuid.uuid4())
    event_key = f"opening_correction:{mid}:{client_request_id}"
    existing = await db.stock_movements.find_one(
        {"$or": [{"event_key": event_key}, {"id": event_key}, {"_id": event_key}]},
        {"_id": 0},
    )
    if existing:
        fresh = await get_product_stock_state(db, mid)
        return {
            "unchanged": abs(d_unp) < 1e-9 and abs(d_pri) < 1e-9,
            "idempotent": True,
            "stock_state": fresh,
            "movement": existing,
            "message": "Koreksi saldo awal sudah tercatat",
        }

    actor = _actor_from_user(user)
    stamp = now_iso()
    old_qty = {"unprinted_qty": old_unp, "printed_qty": old_pri, "total_qty": old_tot}
    new_qty = {"unprinted_qty": new_unp, "printed_qty": new_pri, "total_qty": new_tot}

    if abs(d_unp) < 1e-9 and abs(d_pri) < 1e-9:
        return {
            "unchanged": True,
            "idempotent": False,
            "stock_state": state,
            "movement": None,
            "old_qty": old_qty,
            "new_qty": new_qty,
            "message": "Tidak ada selisih koreksi",
        }

    movement = await record_stock_movement(
        db,
        movement_type=MOVEMENT_KOREKSI,
        master_item_id=mid,
        accurate_item_id=state.get("accurate_item_id") or master.get("accurate_item_id"),
        transaction_qty=d_tot,
        transaction_uom=state.get("base_uom") or master.get("unit") or "PCS",
        source_entity="KOREKSI_SALDO_AWAL",
        destination_entity="GUDANG_UTAMA",
        reference_number=f"KSA-{stamp[:10]}",
        reference_type="opening_balance_correction",
        reference_id=event_key,
        description=note,
        delta_unprinted=d_unp,
        delta_printed=d_pri,
        event_key=event_key,
        user=actor,
        now=stamp,
        movement_id=event_key,
        balance_before=old_tot,
        balance_after=new_tot,
        unprinted_before=old_unp,
        unprinted_after=new_unp,
        printed_before=old_pri,
        printed_after=new_pri,
        source_event_type="OPENING_BALANCE_CORRECTION",
        source_event_id=event_key,
    )

    correction_audit = {
        "old_qty": old_qty,
        "new_qty": new_qty,
        "corrected_by": actor,
        "corrected_at": stamp,
        "note": note,
        "movement_id": movement.get("id") or event_key,
        "client_request_id": client_request_id,
    }
    await db[COLLECTION].update_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]},
        {
            "$set": {
                "unprinted_qty": new_unp,
                "printed_qty": new_pri,
                "total_qty": new_tot,
                "updated_at": stamp,
                "initialized": True,
                "status": STATUS_INITIALIZED,
                "last_opening_correction": correction_audit,
            }
        },
    )
    saved = await get_product_stock_state(db, mid)
    return {
        "unchanged": False,
        "idempotent": False,
        "stock_state": saved,
        "movement": movement,
        "old_qty": old_qty,
        "new_qty": new_qty,
        "corrected_by": actor,
        "corrected_at": stamp,
        "note": note,
        "message": "Koreksi saldo awal stok fisik berhasil",
    }


async def require_product_stock_initialized(db, master_item_id: str) -> Dict[str, Any]:
    """Guard helper for future event hooks.
    
    If physical stock tracking is attempted for an uninitialized product,
    strictly rejects mutation and raises HTTP 400:
    'Saldo awal stok fisik produk belum ditetapkan.'
    """
    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")
    state = await get_product_stock_state(db, mid)
    if not state or not state.get("initialized") or state.get("status") != STATUS_INITIALIZED:
        raise HTTPException(400, f"Saldo awal stok fisik produk belum ditetapkan (ID: '{mid}').")
    return state
