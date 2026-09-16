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
from urllib.parse import urlparse
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
LOCAL_CORRECTION_ENVS = frozenset({"local", "development", "preview", "test"})
LOCAL_MONGO_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
OPENING_CORRECTION_CONFLICT_MSG = "Ada mutasi stok yang sedang berjalan untuk item ini"
OPENING_CORRECTION_SOURCE = "OPENING_BALANCE_CORRECTION"

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


def can_correct_physical_stock(
    user: Optional[Dict[str, Any]],
    app_env: Optional[str],
    mongo_url: Optional[str] = None,
) -> bool:
    role = str((user or {}).get("role") or "").strip().lower()
    env = str(app_env or "").strip().lower()
    try:
        mongo_host = urlparse(str(mongo_url or "")).hostname
    except ValueError:
        mongo_host = None
    explicit_local = env in LOCAL_CORRECTION_ENVS and mongo_host in LOCAL_MONGO_HOSTS
    return role == HOLDING_TEAM_ROLE or (role == "admin" and explicit_local)


def require_physical_stock_correction_access(
    user: Optional[Dict[str, Any]],
    app_env: Optional[str],
    mongo_url: Optional[str] = None,
) -> None:
    if not can_correct_physical_stock(user, app_env, mongo_url):
        raise HTTPException(403, HOLDING_TEAM_ONLY_MSG)


def _opening_correction_conflict() -> HTTPException:
    return HTTPException(409, OPENING_CORRECTION_CONFLICT_MSG)


def _item_state_filter(mid: str) -> Dict[str, Any]:
    return {"$or": [{"_id": mid}, {"master_item_id": mid}]}


def _qty_fields(unprinted: Any, printed: Any, total: Any) -> Dict[str, Any]:
    return {
        "unprinted_qty": unprinted,
        "printed_qty": printed,
        "total_qty": total,
    }


def _pending_event_id(state: Optional[Dict[str, Any]]) -> str:
    pending = (state or {}).get("pending_event")
    if isinstance(pending, dict):
        return str(pending.get("event_id") or "").strip()
    return ""


def _qty_matches(state: Optional[Dict[str, Any]], unprinted: Any, printed: Any, total: Any) -> bool:
    if not state:
        return False
    return (
        abs(safe_float(state.get("unprinted_qty")) - safe_float(unprinted)) < 1e-9
        and abs(safe_float(state.get("printed_qty")) - safe_float(printed)) < 1e-9
        and abs(safe_float(state.get("total_qty")) - safe_float(total)) < 1e-9
    )


async def _find_opening_correction_movement(db, event_key: str) -> Optional[Dict[str, Any]]:
    return await db.stock_movements.find_one(
        {"$or": [{"event_key": event_key}, {"id": event_key}, {"_id": event_key}]},
        {"_id": 0},
    )


async def _claim_opening_correction(db, mid: str, snapshot: Dict[str, Any], claim_data: Dict[str, Any]) -> bool:
    """Atomically occupy pending_event on the exact current physical snapshot.

    Reuses the physical_stock_engine pending_event lock field so an in-flight
    engine mutation cannot be claimed over, and a held correction claim blocks
    the engine's pending_event: None CAS.
    """
    query = _item_state_filter(mid)
    query.update({
        "status": STATUS_INITIALIZED,
        "initialized": True,
        "pending_event": None,
        "unprinted_qty": snapshot["unprinted_qty"],
        "printed_qty": snapshot["printed_qty"],
        "total_qty": snapshot["total_qty"],
    })
    res = await db[COLLECTION].update_one(
        query,
        {"$set": {"pending_event": claim_data, "updated_at": claim_data.get("claimed_at")}},
    )
    return bool(getattr(res, "matched_count", 0))


async def _release_opening_correction_claim(db, mid: str, claim_data: Dict[str, Any]) -> None:
    query = _item_state_filter(mid)
    query["pending_event"] = claim_data
    await db[COLLECTION].update_one(query, {"$set": {"pending_event": None}})


async def _apply_opening_correction_state(
    db,
    mid: str,
    claim_data: Dict[str, Any],
    *,
    new_unp: Any,
    new_pri: Any,
    new_tot: Any,
    stamp: str,
    correction_audit: Dict[str, Any],
    version: Any,
) -> bool:
    """Commit physical buckets and release the claim in one update.

    Ledger must already exist (or this is the no-delta release path).
    Filter requires the exact claim document so a concurrent engine/other
    correction cannot be overwritten.
    """
    old_qty = claim_data.get("old_qty") or {}
    query = _item_state_filter(mid)
    query.update({
        "pending_event": claim_data,
        "unprinted_qty": old_qty.get("unprinted_qty"),
        "printed_qty": old_qty.get("printed_qty"),
        "total_qty": old_qty.get("total_qty"),
    })
    next_version = 1
    try:
        next_version = int(version or 0) + 1
    except (TypeError, ValueError):
        next_version = 1
    res = await db[COLLECTION].update_one(
        query,
        {
            "$set": {
                "unprinted_qty": new_unp,
                "printed_qty": new_pri,
                "total_qty": new_tot,
                "updated_at": stamp,
                "initialized": True,
                "status": STATUS_INITIALIZED,
                "last_opening_correction": correction_audit,
                "pending_event": None,
                "version": next_version,
            }
        },
    )
    return bool(getattr(res, "matched_count", 0))


async def _recover_opening_correction_state(
    db,
    mid: str,
    event_key: str,
    movement: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """If ledger exists and this event still holds pending_event, finish the state commit."""
    raw = await db[COLLECTION].find_one(_item_state_filter(mid))
    if not raw:
        return None
    pending = raw.get("pending_event")
    if not isinstance(pending, dict) or str(pending.get("event_id") or "") != event_key:
        return await get_product_stock_state(db, mid)

    new_qty = pending.get("new_qty") or {}
    new_unp = new_qty.get("unprinted_qty", movement.get("unprinted_after"))
    new_pri = new_qty.get("printed_qty", movement.get("printed_after"))
    new_tot = new_qty.get("total_qty", movement.get("balance_after"))
    stamp = pending.get("claimed_at") or now_iso()
    audit = pending.get("correction_audit") or {
        "old_qty": pending.get("old_qty"),
        "new_qty": new_qty,
        "difference_qty": safe_float(new_tot) - safe_float((pending.get("old_qty") or {}).get("total_qty")),
        "changed_by": pending.get("actor"),
        "changed_at": stamp,
        "corrected_by": pending.get("actor"),
        "corrected_at": stamp,
        "note": pending.get("note") or "",
        "movement_id": movement.get("id") or event_key,
        "client_request_id": pending.get("client_request_id") or "",
    }
    await _apply_opening_correction_state(
        db,
        mid,
        pending,
        new_unp=new_unp,
        new_pri=new_pri,
        new_tot=new_tot,
        stamp=stamp,
        correction_audit=audit,
        version=raw.get("version"),
    )
    return await get_product_stock_state(db, mid)


async def _record_opening_correction_movement(
    db,
    *,
    mid: str,
    master: Dict[str, Any],
    state: Dict[str, Any],
    event_key: str,
    note: str,
    actor: Optional[Dict[str, str]],
    stamp: str,
    old_unp: Any,
    old_pri: Any,
    old_tot: Any,
    new_unp: Any,
    new_pri: Any,
    new_tot: Any,
    d_unp: Any,
    d_pri: Any,
    d_tot: Any,
) -> Dict[str, Any]:
    return await record_stock_movement(
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
        source_event_type=OPENING_CORRECTION_SOURCE,
        source_event_id=event_key,
        change_metadata={
            "old_qty": old_tot,
            "new_qty": new_tot,
            "difference_qty": d_tot,
            "changed_by": actor,
            "changed_at": stamp,
            "change_note": note,
        },
    )


async def correct_opening_balance(
    db,
    master_item_id: str,
    payload: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
    *,
    app_env: Optional[str] = None,
    mongo_url: Optional[str] = None,
) -> Dict[str, Any]:
    """HOLDING_TEAM-only append-only correction of current physical buckets.

    Does NOT delete/overwrite SALDO_AWAL. Does NOT insert a second SALDO_AWAL.
    Does NOT reset initialized. Does NOT change physical_stock settings.
    Target is current physical qty (unprinted + printed), not a history replay.

    Concurrency: claim pending_event against the exact current snapshot, then
    write KOREKSI, then commit buckets and release the claim in one update.
    Distinct concurrent requests cannot both commit from the same before-state.
    """
    require_physical_stock_correction_access(user, app_env, mongo_url)

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

    client_request_id = str((payload or {}).get("client_request_id") or "").strip() or str(uuid.uuid4())
    event_key = f"opening_correction:{mid}:{client_request_id}"
    existing = await _find_opening_correction_movement(db, event_key)
    if existing:
        fresh = await _recover_opening_correction_state(db, mid, event_key, existing)
        return {
            "unchanged": _qty_matches(fresh, new_unp, new_pri, new_tot),
            "idempotent": True,
            "stock_state": fresh,
            "movement": existing,
            "message": "Koreksi saldo awal sudah tercatat",
        }

    state = await get_product_stock_state(db, mid)
    if not state or not state.get("initialized") or state.get("status") != STATUS_INITIALIZED:
        raise HTTPException(400, "Saldo awal stok fisik produk belum ditetapkan")

    held_id = _pending_event_id(state)
    if held_id and held_id != event_key:
        raise _opening_correction_conflict()

    raw_state = await db[COLLECTION].find_one(_item_state_filter(mid))
    pending = (raw_state or {}).get("pending_event") if raw_state else None
    resume_claim = isinstance(pending, dict) and str(pending.get("event_id") or "") == event_key

    cas_unp = (raw_state or state).get("unprinted_qty")
    cas_pri = (raw_state or state).get("printed_qty")
    cas_tot = (raw_state or state).get("total_qty")
    old_unp = safe_float(cas_unp)
    old_pri = safe_float(cas_pri)
    old_tot = safe_float(cas_tot)
    d_unp = new_unp - old_unp
    d_pri = new_pri - old_pri
    d_tot = new_tot - old_tot
    old_qty = _qty_fields(old_unp, old_pri, old_tot)
    new_qty = _qty_fields(new_unp, new_pri, new_tot)
    actor = _actor_from_user(user)
    stamp = now_iso()
    snapshot = _qty_fields(cas_unp, cas_pri, cas_tot)

    claim_data = {
        "event_id": event_key,
        "master_item_id": mid,
        "stage": "CLAIMED",
        "source_event_type": OPENING_CORRECTION_SOURCE,
        "client_request_id": client_request_id,
        "old_qty": old_qty,
        "new_qty": new_qty,
        "note": note,
        "actor": actor,
        "claimed_at": stamp,
    }

    if resume_claim:
        claim_data = pending
        resume_old = claim_data.get("old_qty") or old_qty
        resume_new = claim_data.get("new_qty") or new_qty
        old_unp = safe_float(resume_old.get("unprinted_qty"))
        old_pri = safe_float(resume_old.get("printed_qty"))
        old_tot = safe_float(resume_old.get("total_qty"))
        new_unp = safe_float(resume_new.get("unprinted_qty"), new_unp)
        new_pri = safe_float(resume_new.get("printed_qty"), new_pri)
        new_tot = safe_float(resume_new.get("total_qty"), new_tot)
        d_unp = new_unp - old_unp
        d_pri = new_pri - old_pri
        d_tot = new_tot - old_tot
        old_qty = _qty_fields(old_unp, old_pri, old_tot)
        new_qty = _qty_fields(new_unp, new_pri, new_tot)
        stamp = claim_data.get("claimed_at") or stamp
        actor = claim_data.get("actor") or actor
        note = claim_data.get("note") or note
    else:
        claimed = await _claim_opening_correction(db, mid, snapshot, claim_data)
        if not claimed:
            raced = await _find_opening_correction_movement(db, event_key)
            if raced:
                fresh = await _recover_opening_correction_state(db, mid, event_key, raced)
                return {
                    "unchanged": _qty_matches(fresh, new_unp, new_pri, new_tot),
                    "idempotent": True,
                    "stock_state": fresh,
                    "movement": raced,
                    "message": "Koreksi saldo awal sudah tercatat",
                }
            fresh = await get_product_stock_state(db, mid)
            if _pending_event_id(fresh) == event_key:
                raw_state = await db[COLLECTION].find_one(_item_state_filter(mid))
                pending = (raw_state or {}).get("pending_event")
                if isinstance(pending, dict):
                    claim_data = pending
            else:
                raise _opening_correction_conflict()

    if abs(d_unp) < 1e-9 and abs(d_pri) < 1e-9:
        await _release_opening_correction_claim(db, mid, claim_data)
        fresh = await get_product_stock_state(db, mid)
        return {
            "unchanged": True,
            "idempotent": False,
            "stock_state": fresh or state,
            "movement": None,
            "old_qty": old_qty,
            "new_qty": new_qty,
            "message": "Tidak ada selisih koreksi",
        }

    movement = await _record_opening_correction_movement(
        db,
        mid=mid,
        master=master,
        state=state,
        event_key=event_key,
        note=note,
        actor=actor,
        stamp=stamp,
        old_unp=old_unp,
        old_pri=old_pri,
        old_tot=old_tot,
        new_unp=new_unp,
        new_pri=new_pri,
        new_tot=new_tot,
        d_unp=d_unp,
        d_pri=d_pri,
        d_tot=d_tot,
    )

    correction_audit = {
        "old_qty": old_qty,
        "new_qty": new_qty,
        "difference_qty": d_tot,
        "changed_by": actor,
        "changed_at": stamp,
        "corrected_by": actor,
        "corrected_at": stamp,
        "note": note,
        "movement_id": movement.get("id") or event_key,
        "client_request_id": client_request_id,
    }
    raw_after_claim = await db[COLLECTION].find_one(_item_state_filter(mid))
    stored_for_filter = (raw_after_claim or {}).get("pending_event")
    if not isinstance(stored_for_filter, dict) or str(stored_for_filter.get("event_id") or "") != event_key:
        saved = await _recover_opening_correction_state(db, mid, event_key, movement)
        if not _qty_matches(saved, new_unp, new_pri, new_tot):
            raise HTTPException(
                500,
                "Koreksi tercatat di ledger tetapi gagal menerapkan saldo fisik. Coba lagi.",
            )
        return {
            "unchanged": False,
            "idempotent": True,
            "stock_state": saved,
            "movement": movement,
            "old_qty": old_qty,
            "new_qty": new_qty,
            "corrected_by": actor,
            "corrected_at": stamp,
            "note": note,
            "message": "Koreksi saldo awal stok fisik berhasil",
        }

    applied = await _apply_opening_correction_state(
        db,
        mid,
        stored_for_filter,
        new_unp=new_unp,
        new_pri=new_pri,
        new_tot=new_tot,
        stamp=stamp,
        correction_audit=correction_audit,
        version=(raw_after_claim or {}).get("version"),
    )
    if not applied:
        saved = await _recover_opening_correction_state(db, mid, event_key, movement)
        if not _qty_matches(saved, new_unp, new_pri, new_tot):
            raise HTTPException(
                500,
                "Koreksi tercatat di ledger tetapi gagal menerapkan saldo fisik. Coba lagi.",
            )
        return {
            "unchanged": False,
            "idempotent": True,
            "stock_state": saved,
            "movement": movement,
            "old_qty": old_qty,
            "new_qty": new_qty,
            "corrected_by": actor,
            "corrected_at": stamp,
            "note": note,
            "message": "Koreksi saldo awal stok fisik berhasil",
        }
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
