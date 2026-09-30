"""Authoritative Physical Stock Event Engine & Flexible Recognition Triggers.

Implements:
1. 3-bucket inventory model strictly enforced:
   TOTAL = UNPRINTED + PRINTED (all Base UOM in product_stock_state).
2. STOK_MASUK: All recognized incoming stock enters as BELUM DICETAK / POLOS.
   (total += qty, unprinted += qty, printed += 0)
3. STATUS_CETAK: Operator completion converts Belum Dicetak -> Sudah Dicetak.
   (total += 0, unprinted -= qty, printed += qty)
4. STOK_KELUAR:
   - CETAK deducts from PRINTED (total -= qty, printed -= qty)
   - NON CETAK deducts from UNPRINTED (total -= qty, unprinted -= qty)
5. Zero negative stock permitted across any bucket:
   Any mutation violating stock checks is blocked before business persistence with HTTP 400.
6. Uninitialized products strictly blocked from mutation with HTTP 400:
   "Saldo awal stok fisik produk belum ditetapkan."
7. Snapshot rule:
   Transactions snapshot physical_stock configuration at creation time.
   Legacy documents without snapshot or with enabled=False NEVER mutate stock.
   Future configuration changes do not rewrite history or retroactively backfill.
8. Database-level durable concurrency:
   Uses conditional atomic updates ($gte, $inc, $set) and pending-event protocol.
   Crash recoverable across restarts and multiple workers.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from services.physical_stock_settings import (
    get_physical_stock_settings,
    ALLOWED_STOCK_IN_TRIGGERS,
    ALLOWED_STOCK_OUT_TRIGGERS,
)
from services.product_stock_state import (
    COLLECTION as STATE_COLLECTION,
    STATUS_INITIALIZED,
    get_product_stock_state,
    require_product_stock_initialized,
)
from services.product_unit_conversion import (
    get_product_unit_setting,
    normalize_uom,
)
from services.stock_ledger import (
    MOVEMENT_PENERIMAAN,
    MOVEMENT_REVERSAL_PENERIMAAN,
    MOVEMENT_STATUS_CETAK,
    MOVEMENT_PENGIRIMAN,
    MOVEMENT_KOREKSI,
    record_stock_movement,
    safe_float,
)

logger = logging.getLogger(__name__)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def attach_po_stock_snapshot(db, po_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshots the active physical stock settings onto a new Purchase Order."""
    settings = await get_physical_stock_settings(db)
    snapshot = {
        "enabled": bool(settings.get("enabled")),
        "stock_in_trigger": str(settings.get("stock_in_trigger") or "goods_receipt"),
        "stock_out_trigger": str(settings.get("stock_out_trigger") or "sales_delivery"),
        "snapshotted_at": now_iso(),
    }
    po_doc["physical_stock_snapshot"] = snapshot
    return snapshot


async def attach_order_stock_snapshot(db, order_doc: Dict[str, Any]) -> Dict[str, Any]:
    """Snapshots the active physical stock settings onto a new Sales Order."""
    settings = await get_physical_stock_settings(db)
    snapshot = {
        "enabled": bool(settings.get("enabled")),
        "stock_in_trigger": str(settings.get("stock_in_trigger") or "goods_receipt"),
        "stock_out_trigger": str(settings.get("stock_out_trigger") or "sales_delivery"),
        "snapshotted_at": now_iso(),
    }
    order_doc["physical_stock_snapshot"] = snapshot
    return snapshot


async def resolve_transaction_conversion(
    db,
    master_item_id: str,
    transaction_qty: float,
    transaction_uom: Optional[str] = None,
) -> Tuple[float, float, str, str]:
    """Returns (base_qty, conversion_factor, base_uom, transaction_uom)."""
    mid = str(master_item_id or "").strip()
    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master:
        master = await db.master_items.find_one({"accurate_item_id": mid}, {"_id": 0})
        if master:
            mid = master["id"]

    setting = await get_product_unit_setting(db, mid)
    base_uom = normalize_uom((setting or {}).get("base_uom") or (master or {}).get("unit") or "PCS")
    tx_uom = normalize_uom(str(transaction_uom or (setting or {}).get("transaction_uom") or base_uom))

    if tx_uom == base_uom:
        factor = 1.0
    else:
        factor = safe_float((setting or {}).get("conversion_factor"), 1.0)
        if factor <= 0:
            factor = 1.0

    tx_qty = safe_float(transaction_qty)
    base_qty = safe_float(tx_qty * factor)
    return base_qty, factor, base_uom, tx_uom


async def check_stock_availability(
    db,
    master_item_id: str,
    base_qty: float,
    bucket: str,
    context_label: str = "proses",
) -> Dict[str, Any]:
    """Validates that product is initialized and has sufficient quantity in required bucket.
    
    Raises HTTP 400 immediately if uninitialized or insufficient.
    """
    mid = str(master_item_id or "").strip()
    state = await require_product_stock_initialized(db, mid)

    req = safe_float(base_qty)
    if req <= 0:
        return state

    b = str(bucket or "").strip().lower()
    if b == "unprinted":
        avail = safe_float(state.get("unprinted_qty", 0))
        if avail < req:
            raise HTTPException(
                400,
                f"Stok fisik belum dicetak tidak mencukupi untuk {context_label} "
                f"(Dibutuhkan: {req}, Tersedia: {avail}).",
            )
    elif b == "printed":
        avail = safe_float(state.get("printed_qty", 0))
        if avail < req:
            raise HTTPException(
                400,
                f"Stok fisik sudah dicetak tidak mencukupi untuk {context_label} "
                f"(Dibutuhkan: {req}, Tersedia: {avail}).",
            )
    elif b == "total":
        avail = safe_float(state.get("total_qty", 0))
        if avail < req:
            raise HTTPException(
                400,
                f"Total stok fisik tidak mencukupi untuk {context_label} "
                f"(Dibutuhkan: {req}, Tersedia: {avail}).",
            )

    return state


async def _product_stock_is_initialized(db, master_item_id: str) -> bool:
    """Read-only check matching require_product_stock_initialized semantics.

    True only when state exists, initialized is true, and status is INITIALIZED.
    Used by production-flow wrappers so missing opening stock is a no-op
    instead of HTTP 400. Does not create or mutate stock state.
    """
    mid = str(master_item_id or "").strip()
    if not mid:
        return False
    state = await get_product_stock_state(db, mid)
    return bool(state and state.get("initialized") and state.get("status") == STATUS_INITIALIZED)


async def _production_flow_stock_can_mutate(
    db,
    master_item_id: str,
    transaction_qty: float,
    transaction_uom: Optional[str],
    *,
    movement_type: str,
    is_print: bool,
) -> bool:
    """Read-only: True iff initialized and bucket qty is sufficient for this mutation.

    Missing, uninitialized, or insufficient physical stock → False (skip mutation).
    Does not raise business stock errors. Does not inspect pending_event.
    """
    if not await _product_stock_is_initialized(db, master_item_id):
        return False
    mid = str(master_item_id).strip()
    state = await get_product_stock_state(db, mid)
    if not state:
        return False
    base_qty, _, _, _ = await resolve_transaction_conversion(
        db, mid, transaction_qty, transaction_uom
    )
    req = abs(safe_float(base_qty))
    if req <= 0:
        return True
    m_type = str(movement_type or "").strip().upper()
    if m_type in (MOVEMENT_STATUS_CETAK, "PEMAKAIAN_CETAK"):
        return safe_float(state.get("unprinted_qty", 0)) >= req
    if m_type in (MOVEMENT_PENGIRIMAN, "STOK_KELUAR"):
        if is_print:
            if safe_float(state.get("printed_qty", 0)) < req:
                return False
        else:
            if safe_float(state.get("unprinted_qty", 0)) < req:
                return False
        return safe_float(state.get("total_qty", 0)) >= req
    return False


async def is_source_committed(db, pending_event: Dict[str, Any]) -> bool:
    """Inspects canonical source business records to determine if source committed."""
    ref_type = str(pending_event.get("reference_type") or "").strip().lower()
    order_id = pending_event.get("order_id") or pending_event.get("reference_id")
    action_id = str(pending_event.get("action_id") or "").strip()

    if not order_id:
        return False

    if ref_type in ("operator", "operator_progress", "operator_print", "status_cetak"):
        order = await db.orders.find_one({"id": order_id}, {"_id": 0, "operator_splits": 1})
        if not order:
            return False
        for split in order.get("operator_splits") or []:
            for entry in split.get("progress_entries") or []:
                if action_id and str(entry.get("action_id") or "").strip() == action_id:
                    return True
        return False

    elif ref_type == "loading_confirmation":
        order = await db.orders.find_one({"id": order_id}, {"_id": 0, "loading_confirmations": 1})
        if not order:
            return False
        for conf in order.get("loading_confirmations") or []:
            if action_id and str(conf.get("action_id") or "").strip() == action_id:
                return True
        return False

    elif ref_type == "packing_completed":
        order = await db.orders.find_one({"id": order_id}, {"_id": 0, "packing_info": 1})
        if not order:
            return False
        events = (order.get("packing_info") or {}).get("events") or []
        for ev in events:
            if action_id and str(ev.get("action_id") or "").strip() == action_id:
                return True
        return False

    elif ref_type == "delivery_confirmed":
        order = await db.orders.find_one({"id": order_id}, {"_id": 0, "delivery_confirm_action_ids": 1, "delivery_confirmations": 1})
        if not order:
            return False
        ids = list(order.get("delivery_confirm_action_ids") or [])
        if action_id and action_id in ids:
            return True
        for c in order.get("delivery_confirmations") or []:
            if action_id and str(c.get("action_id") or "").strip() == action_id:
                return True
        return False

    elif ref_type == "goods_receipt":
        ref_id = pending_event.get("reference_id") or pending_event.get("receipt_id")
        if not ref_id:
            return False
        receipt = await db.purchase_goods_receipts.find_one({"id": ref_id}, {"_id": 1})
        return receipt is not None

    elif ref_type == "purchase_order":
        po = await db.purchase_orders.find_one({"id": order_id}, {"_id": 1})
        return po is not None

    return False


async def reconcile_pending_product_claim(
    db,
    master_item_id: str,
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Reconciles an in-flight or interrupted pending_event on product_stock_state."""
    mid = str(master_item_id or "").strip()
    curr = await db[STATE_COLLECTION].find_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]}
    )
    if not curr:
        return {"status": "NO_PENDING"}

    pending = curr.get("pending_event")
    if not pending or not isinstance(pending, dict):
        return {"status": "NO_PENDING"}

    ev_id = pending.get("event_id")
    if not ev_id:
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]},
            {"$unset": {"pending_event": ""}},
        )
        return {"status": "CLEARED", "reason": "CORRUPT_PENDING"}

    existing = await db.stock_movements.find_one(
        {"$or": [{"_id": ev_id}, {"id": ev_id}, {"event_key": ev_id}]},
        {"_id": 0},
    )
    if existing:
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
        return {"status": "FINALIZED", "movement": existing}

    if pending.get("stage") == "STATE_APPLIED":
        mov = await finalize_durable_stock_event(db, event_id=ev_id, master_item_id=mid, user=user)
        return {"status": "FINALIZED", "movement": mov}

    # stage == CLAIMED: verify canonical source commitment
    committed = await is_source_committed(db, pending)
    if committed:
        mov = await finalize_durable_stock_event(db, event_id=ev_id, master_item_id=mid, user=user)
        return {"status": "FINALIZED", "movement": mov}
    else:
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
        return {"status": "CLEARED", "reason": "SOURCE_NOT_COMMITTED"}


async def claim_durable_stock_event(
    db,
    *,
    event_id: str,
    master_item_id: str,
    movement_type: str,
    transaction_qty: float,
    transaction_uom: Optional[str] = None,
    source_entity: str = "",
    destination_entity: str = "",
    reference_number: str = "",
    reference_type: str = "",
    reference_id: Optional[str] = None,
    description: str = "",
    order_id: Optional[str] = None,
    line_idx: Optional[int] = None,
    line_id: Optional[str] = None,
    action_id: Optional[str] = None,
    is_print: Optional[bool] = None,
    production_process: Optional[str] = None,
    change_metadata: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
    fence_generation: Optional[int] = None,
    fence_holder_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Atomically claims / reserves inventory on product_stock_state BEFORE source write.

    Guarantees:
    - Atomically sets pending_event marker with stage 'CLAIMED'
    - Does NOT mutate authoritative total_qty, unprinted_qty, printed_qty prematurely
    - Prevents competing concurrent requests from overspending or acquiring false claims
    - Idempotent: returns existing movement if already finalized
    """
    ev_id = str(event_id or "").strip()
    if not ev_id:
        raise ValueError("event_id wajib diisi")

    # 1. Idempotency check: Already finalized in stock_movements
    existing_movement = await db.stock_movements.find_one(
        {"$or": [{"_id": ev_id}, {"id": ev_id}, {"event_key": ev_id}]},
        {"_id": 0},
    )
    if existing_movement:
        await db[STATE_COLLECTION].update_one(
            {"pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
        return {
            "status": "ALREADY_FINALIZED",
            "movement": existing_movement,
            "event_id": ev_id,
            "master_item_id": master_item_id,
        }

    mid = str(master_item_id or "").strip()
    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master:
        master = await db.master_items.find_one({"accurate_item_id": mid}, {"_id": 0})
        if master:
            mid = master["id"]
        else:
            raise HTTPException(404, f"Produk dengan ID '{mid}' tidak ditemukan.")

    aid = master.get("accurate_item_id")
    await require_product_stock_initialized(db, mid)

    base_qty, factor, base_uom, tx_uom = await resolve_transaction_conversion(
        db, mid, transaction_qty, transaction_uom
    )

    m_type = str(movement_type or "").strip().upper()
    is_p = bool(is_print) if is_print is not None else (
        str(production_process).strip().lower() != "non_print" if production_process else True
    )

    if m_type in (MOVEMENT_PENERIMAAN, "STOK_MASUK"):
        delta_total = abs(base_qty)
        delta_unprinted = abs(base_qty)
        delta_printed = 0.0
    elif m_type == MOVEMENT_REVERSAL_PENERIMAAN:
        delta_total = -abs(base_qty)
        delta_unprinted = -abs(base_qty)
        delta_printed = 0.0
    elif m_type in (MOVEMENT_STATUS_CETAK, "PEMAKAIAN_CETAK"):
        delta_total = 0.0
        delta_unprinted = -abs(base_qty)
        delta_printed = abs(base_qty)
    elif m_type in (MOVEMENT_PENGIRIMAN, "STOK_KELUAR"):
        delta_total = -abs(base_qty)
        if is_p:
            delta_unprinted = 0.0
            delta_printed = -abs(base_qty)
        else:
            delta_unprinted = -abs(base_qty)
            delta_printed = 0.0
    elif m_type == MOVEMENT_KOREKSI:
        delta_total = base_qty
        delta_unprinted = base_qty
        delta_printed = 0.0
    else:
        raise ValueError(f"Tipe mutasi '{movement_type}' tidak didukung di engine")

    stamp = now or now_iso()

    curr = await db[STATE_COLLECTION].find_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]}
    )
    if not curr or not curr.get("initialized") or curr.get("status") != STATUS_INITIALIZED:
        raise HTTPException(400, f"Saldo awal stok fisik produk belum ditetapkan (ID: '{mid}').")

    # 2. Check pending event on product
    pending = curr.get("pending_event")
    if pending and isinstance(pending, dict):
        if pending.get("event_id") == ev_id:
            logger.info("Resuming existing stock claim %s on product %s", ev_id, mid)
            if (
                fence_generation is not None
                and pending.get("fence_generation") is not None
                and int(pending.get("fence_generation")) != int(fence_generation)
            ):
                raise HTTPException(409, DELIVERY_FENCE_LOST)
            if await is_source_committed(db, pending):
                resume_gen = pending.get("fence_generation") if "fence_generation" in pending else None
                mov = await finalize_durable_stock_event(
                    db, event_id=ev_id, master_item_id=mid, user=user,
                    expected_fence_generation=None if resume_gen is None else int(resume_gen),
                )
                return {
                    "status": "ALREADY_FINALIZED",
                    "movement": mov,
                    "event_id": ev_id,
                    "master_item_id": mid,
                }
            return {
                "status": "CLAIMED",
                "event_id": ev_id,
                "master_item_id": mid,
                "accurate_item_id": aid,
                "claim_data": pending,
                "resumed": True,
            }
        else:
            # Different event exists on this product -> BLOCK!
            raise HTTPException(400, "Stok fisik tidak mencukupi atau terjadi konflik konkurensi.")

    # 3. Check sufficient available stock in target bucket
    if delta_unprinted < 0:
        avail_unp = safe_float(curr.get("unprinted_qty", 0))
        if avail_unp < abs(delta_unprinted):
            action_name = "proses cetak" if m_type in (MOVEMENT_STATUS_CETAK, "PEMAKAIAN_CETAK") else "pengiriman"
            raise HTTPException(
                400,
                f"Stok fisik belum dicetak tidak mencukupi untuk {action_name} "
                f"(Dibutuhkan: {abs(delta_unprinted)}, Tersedia: {avail_unp}).",
            )
    if delta_printed < 0:
        avail_pri = safe_float(curr.get("printed_qty", 0))
        if avail_pri < abs(delta_printed):
            raise HTTPException(
                400,
                f"Stok fisik sudah dicetak tidak mencukupi untuk pengiriman "
                f"(Dibutuhkan: {abs(delta_printed)}, Tersedia: {avail_pri}).",
            )
    if delta_total < 0:
        avail_tot = safe_float(curr.get("total_qty", 0))
        if avail_tot < abs(delta_total):
            raise HTTPException(
                400,
                f"Total stok fisik tidak mencukupi untuk mutasi ini "
                f"(Dibutuhkan: {abs(delta_total)}, Tersedia: {avail_tot}).",
            )

    tot_before = safe_float(curr.get("total_qty", 0))
    unp_before = safe_float(curr.get("unprinted_qty", 0))
    pri_before = safe_float(curr.get("printed_qty", 0))

    tot_after = safe_float(tot_before + delta_total)
    unp_after = safe_float(unp_before + delta_unprinted)
    pri_after = safe_float(pri_before + delta_printed)

    claim_data = {
        "event_id": ev_id,
        "master_item_id": mid,
        "stage": "CLAIMED",
        "movement_type": m_type,
        "transaction_qty": transaction_qty,
        "transaction_uom": tx_uom,
        "conversion_factor": factor,
        "base_qty": base_qty,
        "base_uom": base_uom,
        "total_before": tot_before,
        "total_after": tot_after,
        "unprinted_before": unp_before,
        "unprinted_after": unp_after,
        "printed_before": pri_before,
        "printed_after": pri_after,
        "delta_total": delta_total,
        "delta_unprinted": delta_unprinted,
        "delta_printed": delta_printed,
        "source_entity": source_entity,
        "destination_entity": destination_entity,
        "reference_number": reference_number,
        "reference_type": reference_type,
        "reference_id": reference_id,
        "description": description,
        "order_id": order_id,
        "line_idx": line_idx,
        "line_id": line_id,
        "action_id": action_id or "",
        "is_print": is_p,
        "production_process": production_process,
        "change_metadata": dict(change_metadata or {}),
        "accurate_item_id": aid,
        "claimed_at": stamp,
        "started_at": stamp,
    }
    if fence_generation is not None:
        claim_data["fence_generation"] = int(fence_generation)
    if fence_holder_id:
        claim_data["fence_holder_id"] = str(fence_holder_id)

    # 4. Atomic conditional reservation: sets pending_event WITHOUT premature balance mutation!
    query = {
        "$or": [{"_id": mid}, {"master_item_id": mid}],
        "status": STATUS_INITIALIZED,
        "initialized": True,
        "pending_event": None,
    }
    if delta_unprinted < 0:
        query["unprinted_qty"] = {"$gte": abs(delta_unprinted)}
    if delta_printed < 0:
        query["printed_qty"] = {"$gte": abs(delta_printed)}
    if delta_total < 0:
        query["total_qty"] = {"$gte": abs(delta_total)}

    update = {
        "$set": {
            "pending_event": claim_data,
            "updated_at": stamp,
        },
    }

    res = await db[STATE_COLLECTION].find_one_and_update(
        query,
        update,
        return_document=ReturnDocument.AFTER,
    )

    if not res:
        raise HTTPException(400, "Stok fisik tidak mencukupi atau terjadi konflik konkurensi.")

    return {
        "status": "CLAIMED",
        "event_id": ev_id,
        "master_item_id": mid,
        "accurate_item_id": aid,
        "claim_data": claim_data,
        "resumed": False,
    }


async def release_durable_stock_claim(
    db,
    *,
    event_id: str,
    master_item_id: str,
) -> bool:
    """Safely releases an in-flight stock claim if source write fails."""
    ev_id = str(event_id or "").strip()
    mid = str(master_item_id or "").strip()
    curr = await db[STATE_COLLECTION].find_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]}
    )
    if not curr:
        return False
    pending = curr.get("pending_event")
    if not pending or not isinstance(pending, dict) or pending.get("event_id") != ev_id:
        return False

    stage = pending.get("stage", "CLAIMED")
    if stage == "STATE_APPLIED":
        delta_tot = safe_float(pending.get("delta_total", 0.0))
        delta_unp = safe_float(pending.get("delta_unprinted", 0.0))
        delta_pri = safe_float(pending.get("delta_printed", 0.0))
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id},
            {
                "$inc": {
                    "total_qty": -delta_tot,
                    "unprinted_qty": -delta_unp,
                    "printed_qty": -delta_pri,
                    "version": 1,
                },
                "$unset": {"pending_event": ""},
            },
        )
    else:
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
    logger.info("Released stock claim %s on %s successfully", ev_id, mid)
    return True


async def finalize_durable_stock_event(
    db,
    *,
    event_id: str,
    master_item_id: str,
    user: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
    expected_fence_generation: Optional[int] = None,
) -> Dict[str, Any]:
    """Finalizes an active stock claim into stock_movements and unsets pending marker."""
    ev_id = str(event_id or "").strip()
    mid = str(master_item_id or "").strip()

    existing_movement = await db.stock_movements.find_one(
        {"$or": [{"_id": ev_id}, {"id": ev_id}, {"event_key": ev_id}]},
        {"_id": 0},
    )
    if existing_movement:
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
        return existing_movement

    curr = await db[STATE_COLLECTION].find_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]}
    )
    pending = curr.get("pending_event") if curr else None
    if not pending or not isinstance(pending, dict) or pending.get("event_id") != ev_id:
        if existing_movement:
            return existing_movement
        raise RuntimeError(f"Tidak ada klaim stok pending yang cocok untuk event '{ev_id}'.")

    stamp = now or pending.get("started_at") or now_iso()
    stage = pending.get("stage", "CLAIMED")
    if expected_fence_generation is not None:
        if pending.get("fence_generation") is None or int(pending.get("fence_generation")) != int(expected_fence_generation):
            raise RuntimeError(f"Klaim stok '{ev_id}' tidak lagi dimiliki percobaan ini.")
        if str(pending.get("reference_type") or "") != "delivery_confirmed":
            raise RuntimeError(f"Klaim stok '{ev_id}' bukan reservasi Konfirmasi Terkirim.")
        if not await is_source_committed(db, pending):
            raise RuntimeError("Konfirmasi Terkirim belum tersimpan untuk operasi ini.")

    apply_stages = ("CLAIMED", _COMMIT_LOCKED) if expected_fence_generation is not None else ("CLAIMED",)
    if stage in apply_stages:
        d_tot = safe_float(pending.get("delta_total", 0.0))
        d_unp = safe_float(pending.get("delta_unprinted", 0.0))
        d_pri = safe_float(pending.get("delta_printed", 0.0))
        # Transition marker, stored in the same state write as the balance change.
        # It is not a later claim that the database commit happened at this clock.
        applied_marker = now_iso()
        apply_query = {
            "$or": [{"_id": mid}, {"master_item_id": mid}],
            "pending_event.event_id": ev_id,
            "pending_event.stage": stage,
        }
        if expected_fence_generation is not None:
            apply_query["pending_event.fence_generation"] = int(expected_fence_generation)
            apply_query["pending_event.reference_type"] = "delivery_confirmed"

        await db[STATE_COLLECTION].update_one(
            apply_query,
            {
                "$inc": {
                    "total_qty": d_tot,
                    "unprinted_qty": d_unp,
                    "printed_qty": d_pri,
                    "version": 1,
                },
                "$set": {
                    "pending_event.stage": "STATE_APPLIED",
                    "pending_event.stock_applied_at": applied_marker,
                    "updated_at": stamp,
                }
            }
        )
        curr = await db[STATE_COLLECTION].find_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}]}
        )
        pending = curr.get("pending_event") if curr else None
        if not pending or not isinstance(pending, dict) or pending.get("event_id") != ev_id:
            raced = await db.stock_movements.find_one(
                {"$or": [{"_id": ev_id}, {"id": ev_id}, {"event_key": ev_id}]},
                {"_id": 0},
            )
            if raced:
                return raced
            raise RuntimeError(f"Klaim stok '{ev_id}' tidak lagi menunggu penerapan.")
        if pending.get("stage") != "STATE_APPLIED":
            raise RuntimeError(f"Penerapan stok '{ev_id}' tidak tersimpan pada dokumen state.")

    stored_applied = str((pending or {}).get("stock_applied_at") or "").strip()
    applied_at = stored_applied or None

    line_claims = pending.get("line_claims") if isinstance(pending.get("line_claims"), list) else None
    if line_claims:
        movements = []
        run_total = safe_float(pending.get("total_before"))
        run_unprinted = safe_float(pending.get("unprinted_before"))
        run_printed = safe_float(pending.get("printed_before"))
        for spec in line_claims:
            ev = str(spec.get("event_id") or "").strip()
            d_tot = safe_float(spec.get("delta_total"))
            d_unp = safe_float(spec.get("delta_unprinted"))
            d_pri = safe_float(spec.get("delta_printed"))
            before_total, before_unprinted, before_printed = run_total, run_unprinted, run_printed
            after_total = safe_float(before_total + d_tot)
            after_unprinted = safe_float(before_unprinted + d_unp)
            after_printed = safe_float(before_printed + d_pri)
            existing = await _movement_by_event(db, ev)
            if existing:
                mov = existing
            else:
                mov = await record_stock_movement(
                    db,
                    movement_type=pending.get("movement_type") or MOVEMENT_PENGIRIMAN,
                    master_item_id=mid,
                    accurate_item_id=pending.get("accurate_item_id"),
                    transaction_qty=float(spec.get("transaction_qty") or 0.0),
                    transaction_uom=spec.get("transaction_uom") or pending.get("transaction_uom"),
                    source_entity="SUDAH_DICETAK" if spec.get("is_print") else "BELUM_DICETAK",
                    destination_entity=pending.get("destination_entity", ""),
                    reference_number=pending.get("reference_number", ""),
                    reference_id=pending.get("reference_id"),
                    reference_type=pending.get("reference_type", ""),
                    description=spec.get("description") or pending.get("description", ""),
                    order_id=pending.get("order_id"),
                    line_idx=spec.get("line_idx"),
                    line_id=spec.get("line_id"),
                    is_print=spec.get("is_print"),
                    production_process=spec.get("production_process"),
                    delta_unprinted=d_unp,
                    delta_printed=d_pri,
                    event_key=ev,
                    user=user,
                    now=stamp,
                    movement_id=ev,
                    balance_before=before_total,
                    balance_after=after_total,
                    claimed_at=pending.get("claimed_at") or pending.get("started_at"),
                    stock_applied_at=applied_at,
                    unprinted_before=before_unprinted,
                    unprinted_after=after_unprinted,
                    printed_before=before_printed,
                    printed_after=after_printed,
                    source_event_type=pending.get("reference_type", ""),
                    source_event_id=ev,
                    change_metadata=None,
                )
            movements.append(mov)
            run_total, run_unprinted, run_printed = after_total, after_unprinted, after_printed
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id},
            {"$unset": {"pending_event": ""}},
        )
        return {
            "line_movements": movements,
            "movement": movements[-1] if movements else None,
            "stock_applied_at": applied_at,
        }

    m_type = pending.get("movement_type") or MOVEMENT_PENERIMAAN
    t_qty = pending.get("transaction_qty")
    if t_qty is None:
        t_qty = abs(float(pending.get("total_after", 0.0)) - float(pending.get("total_before", 0.0)))
        if t_qty == 0.0:
            t_qty = abs(float(pending.get("printed_after", 0.0)) - float(pending.get("printed_before", 0.0)))

    movement = await record_stock_movement(
        db,
        movement_type=m_type,
        master_item_id=mid,
        accurate_item_id=pending.get("accurate_item_id"),
        transaction_qty=float(t_qty or 0.0),
        transaction_uom=pending.get("transaction_uom"),
        source_entity=pending.get("source_entity", ""),
        destination_entity=pending.get("destination_entity", ""),
        reference_number=pending.get("reference_number", ""),
        reference_id=pending.get("reference_id"),
        reference_type=pending.get("reference_type", ""),
        description=pending.get("description", ""),
        order_id=pending.get("order_id"),
        line_idx=pending.get("line_idx"),
        line_id=pending.get("line_id"),
        is_print=pending.get("is_print"),
        production_process=pending.get("production_process"),
        delta_unprinted=pending.get("delta_unprinted", 0.0),
        delta_printed=pending.get("delta_printed", 0.0),
        event_key=ev_id,
        user=user,
        now=stamp,
        movement_id=ev_id,
        balance_before=pending.get("total_before", 0.0),
        balance_after=pending.get("total_after", 0.0),
        claimed_at=pending.get("claimed_at") or pending.get("started_at"),
        stock_applied_at=applied_at,
        unprinted_before=pending.get("unprinted_before", 0.0),
        unprinted_after=pending.get("unprinted_after", 0.0),
        printed_before=pending.get("printed_before", 0.0),
        printed_after=pending.get("printed_after", 0.0),
        source_event_type=pending.get("reference_type", ""),
        source_event_id=ev_id,
        change_metadata=pending.get("change_metadata"),
    )

    await db[STATE_COLLECTION].update_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id},
        {"$unset": {"pending_event": ""}},
    )
    return movement


async def execute_durable_stock_event(
    db,
    *,
    event_id: str,
    master_item_id: str,
    movement_type: str,
    transaction_qty: float,
    transaction_uom: Optional[str] = None,
    source_entity: str,
    destination_entity: str,
    reference_number: str,
    reference_type: str,
    reference_id: Optional[str] = None,
    description: str = "",
    order_id: Optional[str] = None,
    line_idx: Optional[int] = None,
    line_id: Optional[str] = None,
    action_id: Optional[str] = None,
    is_print: Optional[bool] = None,
    production_process: Optional[str] = None,
    change_metadata: Optional[Dict[str, Any]] = None,
    user: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
) -> Dict[str, Any]:
    """Executes a complete 2-phase durable stock event (claim -> finalize)."""
    claim = await claim_durable_stock_event(
        db,
        event_id=event_id,
        master_item_id=master_item_id,
        movement_type=movement_type,
        transaction_qty=transaction_qty,
        transaction_uom=transaction_uom,
        source_entity=source_entity,
        destination_entity=destination_entity,
        reference_number=reference_number,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        order_id=order_id,
        line_idx=line_idx,
        line_id=line_id,
        action_id=action_id,
        is_print=is_print,
        production_process=production_process,
        change_metadata=change_metadata,
        user=user,
        now=now,
    )
    if claim.get("status") == "ALREADY_FINALIZED":
        return claim["movement"]

    return await finalize_durable_stock_event(
        db,
        event_id=claim["event_id"],
        master_item_id=claim["master_item_id"],
        user=user,
        now=now,
    )


# ==============================================================================
# BUSINESS EVENT HANDLERS & PRE-CHECKS
# ==============================================================================

async def handle_purchase_order_created(
    db,
    po: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluates Stock-In on PO creation according to snapshot rule."""
    snapshot = po.get("physical_stock_snapshot") or {}
    if not snapshot.get("enabled") or snapshot.get("stock_in_trigger") != "purchase_order":
        return []

    po_id = str(po.get("id") or "").strip()
    po_num = str(po.get("po_number") or "").strip()
    supplier = str(po.get("supplier") or "SUPPLIER").strip()

    movements = []
    for line in po.get("lines") or []:
        mid = line.get("master_item_id")
        if not mid:
            continue
        line_id = line.get("line_id") or str(line.get("line_idx", 0))
        event_id = f"stock_in:purchase_order:{po_id}:{line_id}"
        qty = safe_float(line.get("ordered_qty", 0))
        if qty <= 0:
            continue

        mov = await execute_durable_stock_event(
            db,
            event_id=event_id,
            master_item_id=mid,
            movement_type=MOVEMENT_PENERIMAAN,
            transaction_qty=qty,
            transaction_uom=line.get("unit"),
            source_entity=supplier,
            destination_entity="GUDANG_UTAMA",
            reference_number=po_num,
            reference_type="purchase_order",
            reference_id=po_id,
            description=f"Penerimaan PO {po_num}",
            order_id=po_id,
            line_idx=line.get("line_idx"),
            line_id=line.get("line_id"),
            user=user,
        )
        movements.append(mov)

    return movements


async def handle_goods_receipt_created(
    db,
    receipt: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluates Stock-In on Goods Receipt creation according to snapshot rule."""
    po_id = receipt.get("purchase_order_id")
    po = await db.purchase_orders.find_one({"id": po_id}, {"_id": 0}) if po_id else None
    snapshot = (po or {}).get("physical_stock_snapshot") or {}
    if not snapshot.get("enabled") or snapshot.get("stock_in_trigger") != "goods_receipt":
        return []

    rc_id = str(receipt.get("id") or "").strip()
    rc_num = str(receipt.get("receipt_number") or "").strip()
    po_num = str(receipt.get("po_number") or (po or {}).get("po_number") or "").strip()
    supplier = str(receipt.get("supplier") or (po or {}).get("supplier") or "SUPPLIER").strip()

    movements = []
    for line in receipt.get("lines") or []:
        mid = line.get("master_item_id")
        if not mid:
            continue
        line_id = line.get("line_id") or str(line.get("line_idx", 0))
        event_id = f"stock_in:goods_receipt:{rc_id}:{line_id}"
        qty = safe_float(line.get("received_qty", 0))
        if qty <= 0:
            continue

        mov = await execute_durable_stock_event(
            db,
            event_id=event_id,
            master_item_id=mid,
            movement_type=MOVEMENT_PENERIMAAN,
            transaction_qty=qty,
            transaction_uom=line.get("unit"),
            source_entity=supplier,
            destination_entity="GUDANG_UTAMA",
            reference_number=rc_num,
            reference_type="goods_receipt",
            reference_id=rc_id,
            description=f"Penerimaan Barang {rc_num} (PO: {po_num})",
            order_id=po_id,
            line_idx=line.get("line_idx"),
            line_id=line.get("line_id"),
            user=user,
        )
        movements.append(mov)

    return movements


async def handle_manual_receipt_created(
    db,
    receipt: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Stock-In for standalone ERP MANUAL / Penerimaan Langsung receipts.

    Uses live physical_stock settings (no PO snapshot). Idempotent via durable
    event_id keyed by accurate_receipt_id + line_id. Does not replay Accurate.
    """
    settings = await get_physical_stock_settings(db)
    if not settings.get("enabled") or settings.get("stock_in_trigger") != "goods_receipt":
        return []

    rc_id = str(
        receipt.get("accurate_receipt_id")
        or receipt.get("id")
        or ""
    ).strip()
    if not rc_id:
        return []
    rc_num = str(receipt.get("receipt_number") or "").strip()
    supplier = str(receipt.get("supplier") or "SUPPLIER").strip() or "SUPPLIER"

    movements: List[Dict[str, Any]] = []
    for line in receipt.get("lines") or []:
        mid = line.get("master_item_id")
        if not mid:
            continue
        line_id = line.get("line_id") or line.get("accurate_line_id") or str(line.get("line_idx", 0))
        event_id = f"stock_in:goods_receipt:{rc_id}:{line_id}"
        qty = safe_float(line.get("received_qty", 0))
        if qty <= 0:
            continue

        mov = await execute_durable_stock_event(
            db,
            event_id=event_id,
            master_item_id=mid,
            movement_type=MOVEMENT_PENERIMAAN,
            transaction_qty=qty,
            transaction_uom=line.get("unit"),
            source_entity=supplier,
            destination_entity="GUDANG_UTAMA",
            reference_number=rc_num,
            reference_type="goods_receipt",
            reference_id=rc_id,
            description=f"Penerimaan Langsung {rc_num}".strip(),
            line_idx=line.get("line_idx"),
            line_id=line.get("line_id") or line.get("accurate_line_id"),
            user=user,
        )
        movements.append(mov)

    return movements


async def precheck_manual_receipt_lines(
    db,
    lines: List[Dict[str, Any]],
) -> None:
    """Fail-fast before persisting MANUAL receipt when Stock V2 goods_receipt is live."""
    settings = await get_physical_stock_settings(db)
    if not settings.get("enabled") or settings.get("stock_in_trigger") != "goods_receipt":
        return
    for line in lines or []:
        mid = line.get("master_item_id")
        if mid:
            await require_product_stock_initialized(db, mid)


async def is_operator_cetak_physical_stock_active(
    db,
    order: Dict[str, Any],
    *,
    live_enabled: Optional[bool] = None,
) -> bool:
    """Effective Cetak physical-stock gate: live_enabled AND order snapshot.enabled.

    Snapshot short-circuits first. When live_enabled is provided by the request
    path, settings are not re-read. When omitted, live settings are loaded once.
    """
    snap = order.get("physical_stock_snapshot") or {}
    if not snap.get("enabled"):
        return False
    if live_enabled is None:
        settings = await get_physical_stock_settings(db)
        live_enabled = bool(settings.get("enabled"))
    return bool(live_enabled)


async def handle_operator_progress(
    db,
    order: Dict[str, Any],
    split: Dict[str, Any],
    increment: int,
    action_id: str,
    user: Optional[Dict[str, Any]] = None,
    *,
    live_enabled: Optional[bool] = None,
) -> Optional[Dict[str, Any]]:
    """Converts Belum Dicetak -> Sudah Dicetak for print lines on operator progress."""
    if not await is_operator_cetak_physical_stock_active(
        db, order, live_enabled=live_enabled
    ):
        return None

    inc = safe_float(increment)
    if inc <= 0:
        return None

    line_idx = int(split.get("line_idx", 0))
    lis = order.get("line_items") or []
    if not (0 <= line_idx < len(lis)):
        return None

    target_line = lis[line_idx]
    proc = str(target_line.get("production_process") or "").strip().lower()
    if proc == "non_print":
        # NON CETAK requires zero conversion
        return None

    mid = target_line.get("master_item_id")
    if not mid:
        # Check by item_code or accurate_item_id if available
        aid = target_line.get("accurate_item_id")
        master = await db.master_items.find_one({"accurate_item_id": aid}, {"_id": 0}) if aid else None
        if master:
            mid = master["id"]

    if not mid:
        return None
    if not await _production_flow_stock_can_mutate(
        db, mid, inc, target_line.get("unit"),
        movement_type=MOVEMENT_STATUS_CETAK, is_print=True,
    ):
        return None

    order_id = str(order.get("id") or "").strip()
    line_id = str(target_line.get("line_id") or line_idx)
    op_name = split.get("operator_name") or "Operator"
    event_id = f"status_cetak:{order_id}:{line_id}:{action_id}"

    return await execute_durable_stock_event(
        db,
        event_id=event_id,
        master_item_id=mid,
        movement_type=MOVEMENT_STATUS_CETAK,
        transaction_qty=inc,
        transaction_uom=target_line.get("unit"),
        source_entity="BELUM_DICETAK",
        destination_entity="SUDAH_DICETAK",
        reference_number=order.get("po_number") or order_id,
        reference_type="operator_progress",
        reference_id=order_id,
        description=f"Selesai Cetak Operator {op_name} ({order.get('po_number')})",
        order_id=order_id,
        line_idx=line_idx,
        line_id=line_id,
        is_print=True,
        production_process="print",
        user=user,
    )


async def handle_packing_completed(
    db,
    order: Dict[str, Any],
    increment_rows: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluates Stock-Out on Packing Completed according to snapshot rule."""
    snapshot = order.get("physical_stock_snapshot") or {}
    if not snapshot.get("enabled") or snapshot.get("stock_out_trigger") != "packing_completed":
        return []

    order_id = str(order.get("id") or "").strip()
    po_num = str(order.get("po_number") or order_id).strip()
    lis = order.get("line_items") or []

    movements = []
    for inc in increment_rows:
        qty = safe_float(inc.get("qty", 0))
        if qty <= 0:
            continue
        idx = int(inc.get("line_idx", 0))
        if not (0 <= idx < len(lis)):
            continue
        target_line = lis[idx]
        mid = target_line.get("master_item_id")
        if not mid:
            continue
        proc = str(target_line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, target_line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue

        act_id = str(inc.get("action_id") or uuid.uuid4())
        event_id = f"stock_out:packing:{order_id}:{act_id}:{idx}"

        mov = await execute_durable_stock_event(
            db,
            event_id=event_id,
            master_item_id=mid,
            movement_type=MOVEMENT_PENGIRIMAN,
            transaction_qty=qty,
            transaction_uom=target_line.get("unit"),
            source_entity="SUDAH_DICETAK" if is_p else "BELUM_DICETAK",
            destination_entity="PACKING",
            reference_number=po_num,
            reference_type="packing_completed",
            reference_id=order_id,
            description=f"Pengurangan Stok - Packing Selesai ({po_num})",
            order_id=order_id,
            line_idx=idx,
            line_id=target_line.get("line_id"),
            is_print=is_p,
            production_process=proc,
            user=user,
        )
        movements.append(mov)

    return movements


async def handle_loading_confirmed(
    db,
    order: Dict[str, Any],
    record: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluates Stock-Out on Loading Confirmation according to snapshot rule."""
    snapshot = order.get("physical_stock_snapshot") or {}
    if not snapshot.get("enabled") or snapshot.get("stock_out_trigger") != "loading_confirmation":
        return []

    order_id = str(order.get("id") or "").strip()
    po_num = str(order.get("po_number") or order_id).strip()
    rec_id = str(record.get("id") or record.get("action_id") or uuid.uuid4()).strip()
    lis = order.get("line_items") or []

    movements = []
    for line in record.get("lines") or []:
        qty = safe_float(line.get("qty", 0))
        if qty <= 0:
            continue
        idx = int(line.get("line_idx", 0))
        target_line = lis[idx] if (0 <= idx < len(lis)) else line
        mid = target_line.get("master_item_id")
        if not mid:
            continue
        proc = str(target_line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, target_line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue

        event_id = f"stock_out:loading:{order_id}:{rec_id}:{idx}"

        mov = await execute_durable_stock_event(
            db,
            event_id=event_id,
            master_item_id=mid,
            movement_type=MOVEMENT_PENGIRIMAN,
            transaction_qty=qty,
            transaction_uom=target_line.get("unit"),
            source_entity="SUDAH_DICETAK" if is_p else "BELUM_DICETAK",
            destination_entity="LOADING_PENGIRIMAN",
            reference_number=po_num,
            reference_type="loading_confirmation",
            reference_id=order_id,
            description=f"Pengurangan Stok - Loading Dikonfirmasi ({po_num})",
            order_id=order_id,
            line_idx=idx,
            line_id=target_line.get("line_id"),
            is_print=is_p,
            production_process=proc,
            user=user,
        )
        movements.append(mov)

    return movements


async def handle_sales_delivery_created(
    db,
    order: Dict[str, Any],
    delivery: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluates Stock-Out on Sales Delivery (SJ) creation according to snapshot rule."""
    snapshot = order.get("physical_stock_snapshot") or {}
    if not snapshot.get("enabled") or snapshot.get("stock_out_trigger") != "sales_delivery":
        return []

    order_id = str(order.get("id") or "").strip()
    del_id = str(delivery.get("id") or delivery.get("delivery_id") or "").strip()
    sj_number = str(delivery.get("sj_number") or order.get("po_number") or del_id).strip()

    movements = []
    for line in delivery.get("line_items") or []:
        qty = safe_float(line.get("qty", 0))
        if qty <= 0:
            continue
        mid = line.get("master_item_id")
        if not mid:
            # Fallback to matching order line
            idx = int(line.get("line_idx", 0))
            lis = order.get("line_items") or []
            if 0 <= idx < len(lis):
                mid = lis[idx].get("master_item_id")

        if not mid:
            continue
        proc = str(line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue

        line_id = str(line.get("line_id") or line.get("line_idx", 0))
        event_id = f"stock_out:sales_delivery:{del_id}:{line_id}"

        mov = await execute_durable_stock_event(
            db,
            event_id=event_id,
            master_item_id=mid,
            movement_type=MOVEMENT_PENGIRIMAN,
            transaction_qty=qty,
            transaction_uom=line.get("unit"),
            source_entity="SUDAH_DICETAK" if is_p else "BELUM_DICETAK",
            destination_entity=str(delivery.get("customer") or "CUSTOMER"),
            reference_number=sj_number,
            reference_type="sales_delivery",
            reference_id=del_id,
            description=f"Pengiriman Surat Jalan {sj_number} ({order.get('po_number')})",
            order_id=order_id,
            line_idx=line.get("line_idx"),
            line_id=line_id,
            is_print=is_p,
            production_process=proc,
            user=user,
        )
        movements.append(mov)

    return movements


async def handle_delivery_confirmed(
    db,
    order: Dict[str, Any],
    confirmation: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Evaluates Stock-Out on Delivery Confirmed according to snapshot rule."""
    snapshot = order.get("physical_stock_snapshot") or {}
    if not snapshot.get("enabled") or snapshot.get("stock_out_trigger") != "delivery_confirmed":
        return []

    order_id = str(order.get("id") or "").strip()
    po_num = str(order.get("po_number") or order_id).strip()
    conf_id = str(confirmation.get("id") or confirmation.get("action_id") or uuid.uuid4()).strip()
    lis = order.get("line_items") or []

    movements = []
    # If confirmation contains specific line deltas, use them; otherwise use order lines
    lines = confirmation.get("lines") or lis
    for idx, line in enumerate(lines):
        qty = safe_float(line.get("qty", line.get("quantity", 0)))
        if qty <= 0:
            continue
        mid = line.get("master_item_id")
        if not mid and idx < len(lis):
            mid = lis[idx].get("master_item_id")
        if not mid:
            continue
        proc = str(line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue

        event_id = f"stock_out:delivery:{order_id}:{conf_id}:{idx}"

        mov = await execute_durable_stock_event(
            db,
            event_id=event_id,
            master_item_id=mid,
            movement_type=MOVEMENT_PENGIRIMAN,
            transaction_qty=qty,
            transaction_uom=line.get("unit"),
            source_entity="SUDAH_DICETAK" if is_p else "BELUM_DICETAK",
            destination_entity="CUSTOMER",
            reference_number=po_num,
            reference_type="delivery_confirmed",
            reference_id=order_id,
            description=f"Pengurangan Stok - Pengiriman Dikonfirmasi ({po_num})",
            order_id=order_id,
            line_idx=idx,
            line_id=line.get("line_id"),
            is_print=is_p,
            production_process=proc,
            user=user,
        )
        movements.append(mov)

    return movements


# ==============================================================================
# PRE-CHECK GUARDS (Fail-fast before business document persistence)
# ==============================================================================

async def precheck_purchase_order_lines(
    db,
    lines: List[Dict[str, Any]],
    snapshot: Optional[Dict[str, Any]],
) -> None:
    """Pre-checks that all master items in PO lines are initialized if physical stock is enabled with purchase_order trigger."""
    snap = snapshot or {}
    if not snap.get("enabled") or snap.get("stock_in_trigger") != "purchase_order":
        return
    for line in lines:
        mid = line.get("master_item_id")
        if mid:
            await require_product_stock_initialized(db, mid)


async def precheck_goods_receipt_lines(
    db,
    lines: List[Dict[str, Any]],
    po_snapshot: Optional[Dict[str, Any]],
) -> None:
    """Pre-checks that all master items in Receipt lines are initialized if physical stock is enabled with goods_receipt trigger."""
    snap = po_snapshot or {}
    if not snap.get("enabled") or snap.get("stock_in_trigger") != "goods_receipt":
        return
    for line in lines:
        mid = line.get("master_item_id")
        if mid:
            await require_product_stock_initialized(db, mid)


async def precheck_operator_progress(
    db,
    order: Dict[str, Any],
    split: Dict[str, Any],
    increment: int,
    *,
    live_enabled: Optional[bool] = None,
) -> None:
    """Pre-checks that Belum Dicetak stock is available for print conversion."""
    if not await is_operator_cetak_physical_stock_active(
        db, order, live_enabled=live_enabled
    ):
        return

    inc = safe_float(increment)
    if inc <= 0:
        return

    idx = int(split.get("line_idx", 0))
    lis = order.get("line_items") or []
    if not (0 <= idx < len(lis)):
        return

    target_line = lis[idx]
    if str(target_line.get("production_process") or "").strip().lower() == "non_print":
        return

    mid = target_line.get("master_item_id")
    if not mid:
        aid = target_line.get("accurate_item_id")
        master = await db.master_items.find_one({"accurate_item_id": aid}, {"_id": 0}) if aid else None
        if master:
            mid = master["id"]

    if not mid:
        return
    if not await _production_flow_stock_can_mutate(
        db, mid, inc, target_line.get("unit"),
        movement_type=MOVEMENT_STATUS_CETAK, is_print=True,
    ):
        return

    base_qty, _, _, _ = await resolve_transaction_conversion(
        db, mid, inc, target_line.get("unit")
    )
    await check_stock_availability(
        db, mid, base_qty, bucket="unprinted", context_label="proses cetak"
    )


async def precheck_packing_completed(
    db,
    order: Dict[str, Any],
    increment_rows: List[Dict[str, Any]],
) -> None:
    """Pre-checks stock availability before packing confirmation is persisted."""
    snap = order.get("physical_stock_snapshot") or {}
    if not snap.get("enabled") or snap.get("stock_out_trigger") != "packing_completed":
        return

    lis = order.get("line_items") or []
    for inc in increment_rows:
        qty = safe_float(inc.get("qty", 0))
        if qty <= 0:
            continue
        idx = int(inc.get("line_idx", 0))
        if not (0 <= idx < len(lis)):
            continue
        target_line = lis[idx]
        mid = target_line.get("master_item_id")
        if not mid:
            continue
        proc = str(target_line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, target_line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue
        bucket = "printed" if is_p else "unprinted"
        base_qty, _, _, _ = await resolve_transaction_conversion(
            db, mid, qty, target_line.get("unit")
        )
        await check_stock_availability(
            db, mid, base_qty, bucket=bucket, context_label="pengurangan stok packing"
        )


async def precheck_loading_confirmed(
    db,
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
) -> None:
    """Pre-checks stock availability before loading confirmation is persisted."""
    snap = order.get("physical_stock_snapshot") or {}
    if not snap.get("enabled") or snap.get("stock_out_trigger") != "loading_confirmation":
        return

    lis = order.get("line_items") or []
    for line in lines:
        qty = safe_float(line.get("qty", 0))
        if qty <= 0:
            continue
        idx = int(line.get("line_idx", 0))
        target_line = lis[idx] if (0 <= idx < len(lis)) else line
        mid = target_line.get("master_item_id")
        if not mid:
            continue
        proc = str(target_line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, target_line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue
        bucket = "printed" if is_p else "unprinted"
        base_qty, _, _, _ = await resolve_transaction_conversion(
            db, mid, qty, target_line.get("unit")
        )
        await check_stock_availability(
            db, mid, base_qty, bucket=bucket, context_label="pengurangan stok loading"
        )


async def precheck_sales_delivery(
    db,
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
) -> None:
    """Pre-checks stock availability before sales delivery (SJ) is persisted."""
    snap = order.get("physical_stock_snapshot") or {}
    if not snap.get("enabled") or snap.get("stock_out_trigger") != "sales_delivery":
        return

    lis = order.get("line_items") or []
    for line in lines:
        qty = safe_float(line.get("qty", 0))
        if qty <= 0:
            continue
        mid = line.get("master_item_id")
        if not mid:
            idx = int(line.get("line_idx", 0))
            if 0 <= idx < len(lis):
                mid = lis[idx].get("master_item_id")
        if not mid:
            continue
        proc = str(line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue
        bucket = "printed" if is_p else "unprinted"
        base_qty, _, _, _ = await resolve_transaction_conversion(
            db, mid, qty, line.get("unit")
        )
        await check_stock_availability(
            db, mid, base_qty, bucket=bucket, context_label="pengiriman Surat Jalan"
        )


def _delivery_claim_specs(
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
    action_id: str,
) -> List[Dict[str, Any]]:
    lis = order.get("line_items") or []
    order_id = str(order.get("id") or "").strip()
    specs: List[Dict[str, Any]] = []
    for row_i, line in enumerate(lines or []):
        if not isinstance(line, dict):
            continue
        qty = safe_float(line.get("qty", line.get("quantity", 0)))
        if qty <= 0:
            continue
        canonical = row_i
        raw_idx = line.get("line_idx")
        if raw_idx is not None and str(raw_idx).strip() != "":
            try:
                canonical = int(raw_idx)
            except (TypeError, ValueError):
                canonical = row_i
        mid = str(line.get("master_item_id") or "").strip()
        if not mid and 0 <= canonical < len(lis) and isinstance(lis[canonical], dict):
            mid = str(lis[canonical].get("master_item_id") or "").strip()
        if not mid:
            continue
        proc = str(line.get("production_process") or "").strip().lower()
        specs.append({
            "idx": canonical,
            "qty": qty,
            "mid": mid,
            "is_print": proc != "non_print",
            "proc": proc,
            "line_id": line.get("line_id"),
            "unit": line.get("unit"),
            "event_id": f"stock_out:delivery:{order_id}:{action_id}:{row_i}",
        })
    return specs


async def _delivery_line_deltas(db, spec: Dict[str, Any]) -> Dict[str, Any]:
    base_qty, factor, base_uom, tx_uom = await resolve_transaction_conversion(
        db, spec["mid"], spec["qty"], spec.get("unit")
    )
    required = abs(safe_float(base_qty))
    if spec["is_print"]:
        delta_unprinted, delta_printed = 0.0, -required
    else:
        delta_unprinted, delta_printed = -required, 0.0
    enriched = dict(spec)
    enriched.update({
        "base_qty": safe_float(base_qty),
        "factor": factor,
        "base_uom": base_uom,
        "tx_uom": tx_uom,
        "delta_total": -required,
        "delta_unprinted": delta_unprinted,
        "delta_printed": delta_printed,
    })
    return enriched


async def _movement_by_event(db, event_id: str) -> Optional[Dict[str, Any]]:
    ev_id = str(event_id or "").strip()
    if not ev_id:
        return None
    return await db.stock_movements.find_one(
        {"$or": [{"_id": ev_id}, {"id": ev_id}, {"event_key": ev_id}]},
        {"_id": 0},
    )


def _delivery_group_event_id(order_id: str, action_id: str, master_item_id: str) -> str:
    return f"stock_out:delivery:{order_id}:{action_id}:master:{master_item_id}"


async def _pending_covers_delivery_spec(db, order_id: str, action_id: str, spec: Dict[str, Any]) -> bool:
    """True when this line is already inside the same action's durable reservation.

    Current bucket qty is irrelevant: the reservation may already have been applied.
    """
    state = await get_product_stock_state(db, spec["mid"])
    pending = (state or {}).get("pending_event") if state else None
    if not isinstance(pending, dict):
        return False
    if str(pending.get("action_id") or "") != str(action_id or ""):
        return False
    if str(pending.get("order_id") or "") != str(order_id or ""):
        return False
    ev = str(pending.get("event_id") or "")
    line_ev = str(spec.get("event_id") or "")
    group_ev = _delivery_group_event_id(order_id, action_id, spec["mid"])
    rows = pending.get("line_claims") if isinstance(pending.get("line_claims"), list) else []
    stored = {str(row.get("event_id") or "") for row in rows if isinstance(row, dict)}
    if ev == group_ev and stored:
        return line_ev in stored
    return ev == line_ev


async def _delivery_spec_is_claimable(
    db,
    spec: Dict[str, Any],
    *,
    order_id: str,
    action_id: str,
    replay: bool = False,
) -> bool:
    """A new reservation needs initialized stock. Recovery does not.

    An existing movement or the same action's pending keeps the line in the
    group even after the bucket has already been reduced to zero. A recorded
    confirmation does not open a new reservation for a line that was skipped.
    """
    if await _movement_by_event(db, spec.get("event_id")):
        return True
    if await _pending_covers_delivery_spec(db, order_id, action_id, spec):
        return True
    if replay:
        return False
    return await _production_flow_stock_can_mutate(
        db,
        spec["mid"],
        spec["qty"],
        spec.get("unit"),
        movement_type=MOVEMENT_PENGIRIMAN,
        is_print=spec["is_print"],
    )


async def precheck_delivery_confirmed(
    db,
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
) -> None:
    """Pre-checks stock availability before delivery confirmation is persisted."""
    snap = order.get("physical_stock_snapshot") or {}
    if not snap.get("enabled") or snap.get("stock_out_trigger") != "delivery_confirmed":
        return

    lis = order.get("line_items") or []
    for idx, line in enumerate(lines):
        qty = safe_float(line.get("qty", line.get("quantity", 0)))
        if qty <= 0:
            continue
        mid = line.get("master_item_id")
        if not mid and idx < len(lis):
            mid = lis[idx].get("master_item_id")
        if not mid:
            continue
        proc = str(line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"
        if not await _production_flow_stock_can_mutate(
            db, mid, qty, line.get("unit"),
            movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
        ):
            continue
        bucket = "printed" if is_p else "unprinted"
        base_qty, _, _, _ = await resolve_transaction_conversion(
            db, mid, qty, line.get("unit")
        )
        await check_stock_availability(
            db, mid, base_qty, bucket=bucket, context_label="konfirmasi pengiriman"
        )


# ==============================================================================
# ATOMIC STOCK CLAIM & FINALIZATION HELPERS FOR ENDPOINTS
# ==============================================================================

async def should_claim_operator_stock(
    db,
    order: Dict[str, Any],
    split: Dict[str, Any],
    *,
    live_enabled: Optional[bool] = None,
) -> bool:
    if not await is_operator_cetak_physical_stock_active(
        db, order, live_enabled=live_enabled
    ):
        return False
    idx = int(split.get("line_idx", 0))
    lis = order.get("line_items") or []
    if not (0 <= idx < len(lis)):
        return False
    target_line = lis[idx]
    if str(target_line.get("production_process") or "").strip().lower() == "non_print":
        return False
    return True


async def claim_operator_stock(
    db,
    order: Dict[str, Any],
    split: Dict[str, Any],
    increment: int,
    action_id: str,
    user: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    inc = safe_float(increment)
    if inc <= 0:
        return None

    idx = int(split.get("line_idx", 0))
    lis = order.get("line_items") or []
    if not (0 <= idx < len(lis)):
        return None

    target_line = lis[idx]
    mid = target_line.get("master_item_id")
    if not mid:
        aid = target_line.get("accurate_item_id")
        master = await db.master_items.find_one({"accurate_item_id": aid}, {"_id": 0}) if aid else None
        if master:
            mid = master["id"]
    if not mid:
        return None
    if not await _production_flow_stock_can_mutate(
        db, mid, inc, target_line.get("unit"),
        movement_type=MOVEMENT_STATUS_CETAK, is_print=True,
    ):
        return None

    event_id = f"status_cetak:{order['id']}:{idx}:{action_id}"
    return await claim_durable_stock_event(
        db,
        event_id=event_id,
        master_item_id=mid,
        movement_type=MOVEMENT_STATUS_CETAK,
        transaction_qty=inc,
        transaction_uom=target_line.get("unit"),
        source_entity="BELUM_DICETAK",
        destination_entity="SUDAH_DICETAK",
        reference_number=str(order.get("po_number") or order.get("id")),
        reference_type="operator_progress",
        reference_id=order.get("id"),
        description=f"Konversi Status Cetak Operator ({split.get('operator_name') or 'Operator'})",
        order_id=order.get("id"),
        line_idx=idx,
        line_id=target_line.get("line_id"),
        action_id=action_id,
        is_print=True,
        production_process="print",
        user=user,
    )


async def release_operator_stock(
    db,
    claimed: Optional[Dict[str, Any]],
) -> None:
    if not claimed or claimed.get("status") != "CLAIMED":
        return
    await release_durable_stock_claim(
        db,
        event_id=claimed["event_id"],
        master_item_id=claimed["master_item_id"],
    )


async def finalize_operator_stock(
    db,
    claimed: Optional[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if not claimed:
        return None
    if claimed.get("status") == "ALREADY_FINALIZED":
        return claimed.get("movement")
    return await finalize_durable_stock_event(
        db,
        event_id=claimed["event_id"],
        master_item_id=claimed["master_item_id"],
        user=user,
    )


async def should_claim_loading_stock(
    db,
    order: Dict[str, Any],
) -> bool:
    snap = order.get("physical_stock_snapshot") or {}
    return bool(snap.get("enabled") and snap.get("stock_out_trigger") == "loading_confirmation")


async def claim_loading_stock(
    db,
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
    action_id: str,
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    claims = []
    lis = order.get("line_items") or []
    try:
        for line in lines:
            qty = safe_float(line.get("qty", 0))
            if qty <= 0:
                continue
            idx = int(line.get("line_idx", 0))
            target_line = lis[idx] if (0 <= idx < len(lis)) else line
            mid = target_line.get("master_item_id")
            if not mid:
                continue
            proc = str(target_line.get("production_process") or "").strip().lower()
            is_p = proc != "non_print"
            if not await _production_flow_stock_can_mutate(
                db, mid, qty, target_line.get("unit"),
                movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
            ):
                continue
            event_id = f"loading:{order['id']}:{action_id}:{idx}"
            claim = await claim_durable_stock_event(
                db,
                event_id=event_id,
                master_item_id=mid,
                movement_type=MOVEMENT_PENGIRIMAN,
                transaction_qty=qty,
                transaction_uom=target_line.get("unit"),
                source_entity="SUDAH_DICETAK" if is_p else "BELUM_DICETAK",
                destination_entity="CUSTOMER",
                reference_number=str(order.get("po_number") or order.get("id")),
                reference_type="loading_confirmation",
                reference_id=order.get("id"),
                description=f"Pengurangan Stok Fisik Konfirmasi Muat ({'Cetak' if is_p else 'Polos'})",
                order_id=order.get("id"),
                line_idx=idx,
                line_id=target_line.get("line_id"),
                action_id=action_id,
                is_print=is_p,
                production_process=proc,
                user=user,
            )
            claims.append(claim)
        return claims
    except Exception:
        await release_loading_stock(db, claims)
        raise


async def release_loading_stock(
    db,
    claims: List[Dict[str, Any]],
) -> None:
    for c in claims:
        if c and c.get("status") == "CLAIMED":
            await release_durable_stock_claim(
                db,
                event_id=c["event_id"],
                master_item_id=c["master_item_id"],
            )


async def finalize_loading_stock(
    db,
    claims: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    movements = []
    for c in claims:
        if not c:
            continue
        if c.get("status") == "ALREADY_FINALIZED":
            movements.append(c.get("movement"))
            continue
        mov = await finalize_durable_stock_event(
            db,
            event_id=c["event_id"],
            master_item_id=c["master_item_id"],
            user=user,
        )
        movements.append(mov)
    return movements


async def should_claim_packing_stock(
    db,
    order: Dict[str, Any],
) -> bool:
    snap = order.get("physical_stock_snapshot") or {}
    return bool(snap.get("enabled") and snap.get("stock_out_trigger") == "packing_completed")


async def claim_packing_stock(
    db,
    order: Dict[str, Any],
    increment_rows: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    claims = []
    lis = order.get("line_items") or []
    try:
        for inc in increment_rows:
            qty = safe_float(inc.get("qty", 0))
            if qty <= 0:
                continue
            idx = int(inc.get("line_idx", 0))
            if not (0 <= idx < len(lis)):
                continue
            target_line = lis[idx]
            mid = target_line.get("master_item_id")
            if not mid:
                continue
            act_id = inc.get("action_id") or str(uuid.uuid4())
            proc = str(target_line.get("production_process") or "").strip().lower()
            is_p = proc != "non_print"
            if not await _production_flow_stock_can_mutate(
                db, mid, qty, target_line.get("unit"),
                movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
            ):
                continue
            event_id = f"packing:{order['id']}:{act_id}:{idx}"
            claim = await claim_durable_stock_event(
                db,
                event_id=event_id,
                master_item_id=mid,
                movement_type=MOVEMENT_PENGIRIMAN,
                transaction_qty=qty,
                transaction_uom=target_line.get("unit"),
                source_entity="SUDAH_DICETAK" if is_p else "BELUM_DICETAK",
                destination_entity="CUSTOMER",
                reference_number=str(order.get("po_number") or order.get("id")),
                reference_type="packing_completed",
                reference_id=order.get("id"),
                description=f"Pengurangan Stok Fisik Packing ({'Cetak' if is_p else 'Polos'})",
                order_id=order.get("id"),
                line_idx=idx,
                line_id=target_line.get("line_id"),
                action_id=act_id,
                is_print=is_p,
                production_process=proc,
                user=user,
            )
            claims.append(claim)
        return claims
    except Exception:
        await release_packing_stock(db, claims)
        raise


async def release_packing_stock(
    db,
    claims: List[Dict[str, Any]],
) -> None:
    for c in claims:
        if c and c.get("status") == "CLAIMED":
            await release_durable_stock_claim(
                db,
                event_id=c["event_id"],
                master_item_id=c["master_item_id"],
            )


async def finalize_packing_stock(
    db,
    claims: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    movements = []
    for c in claims:
        if not c:
            continue
        if c.get("status") == "ALREADY_FINALIZED":
            movements.append(c.get("movement"))
            continue
        mov = await finalize_durable_stock_event(
            db,
            event_id=c["event_id"],
            master_item_id=c["master_item_id"],
            user=user,
        )
        movements.append(mov)
    return movements


async def should_claim_sales_delivery_stock(
    db,
    order: Dict[str, Any],
) -> bool:
    snap = order.get("physical_stock_snapshot") or {}
    return bool(snap.get("enabled") and snap.get("stock_out_trigger") == "sales_delivery")


async def claim_sales_delivery_stock(
    db,
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
    delivery_id: str,
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    claims = []
    lis = order.get("line_items") or []
    try:
        for line in lines:
            qty = safe_float(line.get("qty", 0))
            if qty <= 0:
                continue
            mid = line.get("master_item_id")
            idx = int(line.get("line_idx", 0))
            if not mid and 0 <= idx < len(lis):
                mid = lis[idx].get("master_item_id")
            if not mid:
                continue
            proc = str(line.get("production_process") or "").strip().lower()
            is_p = proc != "non_print"
            if not await _production_flow_stock_can_mutate(
                db, mid, qty, line.get("unit"),
                movement_type=MOVEMENT_PENGIRIMAN, is_print=is_p,
            ):
                continue
            line_id = str(line.get("line_id") or line.get("line_idx", 0))
            event_id = f"stock_out:sales_delivery:{delivery_id}:{line_id}"
            claim = await claim_durable_stock_event(
                db,
                event_id=event_id,
                master_item_id=mid,
                movement_type=MOVEMENT_PENGIRIMAN,
                transaction_qty=qty,
                transaction_uom=line.get("unit"),
                source_entity="SUDAH_DICETAK" if is_p else "BELUM_DICETAK",
                destination_entity="CUSTOMER",
                reference_number=str(order.get("po_number") or order.get("id")),
                reference_type="sales_delivery",
                reference_id=delivery_id,
                description=f"Pengurangan Stok Fisik Surat Jalan ({'Cetak' if is_p else 'Polos'})",
                order_id=order.get("id"),
                line_idx=idx,
                line_id=line_id,
                is_print=is_p,
                production_process=proc,
                user=user,
            )
            claims.append(claim)
        return claims
    except Exception:
        await release_sales_delivery_stock(db, claims)
        raise


async def release_sales_delivery_stock(
    db,
    claims: List[Dict[str, Any]],
) -> None:
    for c in claims:
        if c and c.get("status") == "CLAIMED":
            await release_durable_stock_claim(
                db,
                event_id=c["event_id"],
                master_item_id=c["master_item_id"],
            )


async def finalize_sales_delivery_stock(
    db,
    claims: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    movements = []
    for c in claims:
        if not c:
            continue
        if c.get("status") == "ALREADY_FINALIZED":
            movements.append(c.get("movement"))
            continue
        mov = await finalize_durable_stock_event(
            db,
            event_id=c["event_id"],
            master_item_id=c["master_item_id"],
            user=user,
        )
        movements.append(mov)
    return movements


async def should_claim_delivery_confirmed_stock(
    db,
    order: Dict[str, Any],
) -> bool:
    snap = order.get("physical_stock_snapshot") or {}
    return bool(snap.get("enabled") and snap.get("stock_out_trigger") == "delivery_confirmed")


async def _reserve_delivery_master_group(
    db,
    order: Dict[str, Any],
    action_id: str,
    lines: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """One pending_event for every line of the same master from the same claim source.

    Line identity stays on line_claims and becomes one movement per line at
    finalize. A different pending_event is left untouched.
    """
    mid = lines[0]["mid"]
    order_id = str(order.get("id") or "").strip()
    group_event_id = _delivery_group_event_id(order_id, action_id, mid)
    found = []
    missing = False
    for spec in lines:
        existing = await _movement_by_event(db, spec["event_id"])
        if existing:
            found.append(existing)
        else:
            missing = True
    if found and not missing:
        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": group_event_id},
            {"$unset": {"pending_event": ""}},
        )
        return {
            "status": "ALREADY_FINALIZED",
            "event_id": group_event_id,
            "master_item_id": mid,
            "grouped": True,
            "movement": found[-1],
            "line_movements": found,
        }
    if found and missing:
        curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
        pending = (curr or {}).get("pending_event") if curr else None
        if not (isinstance(pending, dict) and pending.get("event_id") == group_event_id):
            raise RuntimeError(
                f"Klaim pengiriman '{group_event_id}' terputus: sebagian movement ada tanpa pending yang cocok."
            )

    fence = await _fence_stamp_for_action(db, order_id, action_id)
    curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
    pending = (curr or {}).get("pending_event") if curr else None
    if isinstance(pending, dict) and pending.get("event_id"):
        if pending.get("event_id") != group_event_id:
            raise HTTPException(400, "Stok fisik tidak mencukupi atau terjadi konflik konkurensi.")
        stored = [str(row.get("event_id") or "") for row in (pending.get("line_claims") or [])]
        wanted = [spec["event_id"] for spec in lines]
        if stored and stored != wanted:
            raise HTTPException(400, "Klaim pengiriman untuk action ini sudah tersimpan dengan baris berbeda.")
        if (
            fence
            and pending.get("fence_generation") is not None
            and int(pending.get("fence_generation")) != int(fence["generation"])
        ):
            raise HTTPException(409, DELIVERY_FENCE_LOST)
        if await is_source_committed(db, pending):
            resume_gen = pending.get("fence_generation") if "fence_generation" in pending else None
            finalized = await finalize_durable_stock_event(
                db, event_id=group_event_id, master_item_id=mid, user=user,
                expected_fence_generation=None if resume_gen is None else int(resume_gen),
            )
            movements = finalized.get("line_movements") if isinstance(finalized, dict) else None
            return {
                "status": "ALREADY_FINALIZED",
                "event_id": group_event_id,
                "master_item_id": mid,
                "grouped": True,
                "movement": (finalized or {}).get("movement") if isinstance(finalized, dict) else finalized,
                "line_movements": movements or ([finalized] if finalized else []),
            }
        return {
            "status": "CLAIMED",
            "event_id": group_event_id,
            "master_item_id": mid,
            "grouped": True,
            "claim_data": pending,
            "resumed": True,
        }

    curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
    if not curr or not curr.get("initialized") or curr.get("status") != STATUS_INITIALIZED:
        raise HTTPException(400, f"Saldo awal stok fisik produk belum ditetapkan (ID: '{mid}').")

    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    aid = (master or {}).get("accurate_item_id")
    delta_total = sum(safe_float(line.get("delta_total")) for line in lines)
    delta_unprinted = sum(safe_float(line.get("delta_unprinted")) for line in lines)
    delta_printed = sum(safe_float(line.get("delta_printed")) for line in lines)
    tot_before = safe_float(curr.get("total_qty", 0))
    unp_before = safe_float(curr.get("unprinted_qty", 0))
    pri_before = safe_float(curr.get("printed_qty", 0))
    stamp = now_iso()
    ref_number = str(order.get("po_number") or order.get("id"))
    line_claims = []
    for spec in lines:
        line_claims.append({
            "event_id": spec["event_id"],
            "line_idx": spec["idx"],
            "line_id": spec.get("line_id"),
            "transaction_qty": spec["qty"],
            "transaction_uom": spec.get("tx_uom"),
            "base_qty": spec.get("base_qty"),
            "delta_total": spec.get("delta_total"),
            "delta_unprinted": spec.get("delta_unprinted"),
            "delta_printed": spec.get("delta_printed"),
            "is_print": spec["is_print"],
            "production_process": spec.get("proc"),
            "description": (
                "Pengurangan Stok Fisik Konfirmasi Diterima "
                f"({'Cetak' if spec['is_print'] else 'Polos'})"
            ),
        })
    claim_data = {
        "event_id": group_event_id,
        "master_item_id": mid,
        "stage": "CLAIMED",
        "movement_type": MOVEMENT_PENGIRIMAN,
        "transaction_qty": sum(safe_float(line.get("qty")) for line in lines),
        "transaction_uom": lines[0].get("tx_uom"),
        "conversion_factor": lines[0].get("factor"),
        "base_qty": sum(abs(safe_float(line.get("delta_total"))) for line in lines),
        "base_uom": lines[0].get("base_uom"),
        "total_before": tot_before,
        "total_after": safe_float(tot_before + delta_total),
        "unprinted_before": unp_before,
        "unprinted_after": safe_float(unp_before + delta_unprinted),
        "printed_before": pri_before,
        "printed_after": safe_float(pri_before + delta_printed),
        "delta_total": delta_total,
        "delta_unprinted": delta_unprinted,
        "delta_printed": delta_printed,
        "source_entity": "CAMPURAN" if any(line["is_print"] != lines[0]["is_print"] for line in lines) else (
            "SUDAH_DICETAK" if lines[0]["is_print"] else "BELUM_DICETAK"
        ),
        "destination_entity": "CUSTOMER",
        "reference_number": ref_number,
        "reference_type": "delivery_confirmed",
        "reference_id": order_id,
        "description": f"Pengurangan Stok Fisik Konfirmasi Diterima ({ref_number})",
        "order_id": order_id,
        "line_idx": lines[0]["idx"],
        "line_id": lines[0].get("line_id"),
        "action_id": action_id or "",
        "is_print": all(line["is_print"] for line in lines),
        "accurate_item_id": aid,
        "line_claims": line_claims,
        "claimed_at": stamp,
        "started_at": stamp,
    }
    if fence:
        claim_data["fence_generation"] = int(fence["generation"])
        claim_data["fence_holder_id"] = fence.get("holder_id")
    query = {
        "$or": [{"_id": mid}, {"master_item_id": mid}],
        "status": STATUS_INITIALIZED,
        "initialized": True,
        "pending_event": None,
    }
    if delta_unprinted < 0:
        query["unprinted_qty"] = {"$gte": abs(delta_unprinted)}
    if delta_printed < 0:
        query["printed_qty"] = {"$gte": abs(delta_printed)}
    if delta_total < 0:
        query["total_qty"] = {"$gte": abs(delta_total)}
    res = await db[STATE_COLLECTION].find_one_and_update(
        query,
        {"$set": {"pending_event": claim_data, "updated_at": stamp}},
        return_document=ReturnDocument.AFTER,
    )
    if not res:
        raise HTTPException(400, "Stok fisik tidak mencukupi atau terjadi konflik konkurensi.")
    if fence and fence.get("holder_id"):
        await _note_delivery_master(db, fence.get("holder_id"), mid)
    return {
        "status": "CLAIMED",
        "event_id": group_event_id,
        "master_item_id": mid,
        "grouped": True,
        "claim_data": claim_data,
        "resumed": False,
    }


async def claim_delivery_confirmed_stock(
    db,
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
    action_id: str,
    user: Optional[Dict[str, Any]] = None,
    *,
    replay: bool = False,
) -> List[Dict[str, Any]]:
    claims = []
    try:
        grouped: Dict[str, List[Dict[str, Any]]] = {}
        order_id = str(order.get("id") or "")
        for spec in _delivery_claim_specs(order, lines, action_id):
            if not await _delivery_spec_is_claimable(
                db, spec, order_id=order_id, action_id=action_id, replay=replay,
            ):
                continue
            grouped.setdefault(spec["mid"], []).append(spec)
        fence = await _fence_stamp_for_action(db, order_id, action_id)
        for mid, group in grouped.items():
            enriched = [await _delivery_line_deltas(db, spec) for spec in group]
            if (
                len(enriched) > 1
                and not replay
                and await _group_should_skip_for_short_stock(db, order_id, action_id, enriched)
            ):
                continue
            if len(enriched) == 1:
                spec = enriched[0]
                claim = await claim_durable_stock_event(
                    db,
                    event_id=spec["event_id"],
                    master_item_id=mid,
                    movement_type=MOVEMENT_PENGIRIMAN,
                    transaction_qty=spec["qty"],
                    transaction_uom=spec.get("unit"),
                    source_entity="SUDAH_DICETAK" if spec["is_print"] else "BELUM_DICETAK",
                    destination_entity="CUSTOMER",
                    reference_number=str(order.get("po_number") or order.get("id")),
                    reference_type="delivery_confirmed",
                    reference_id=order.get("id"),
                    description=(
                        "Pengurangan Stok Fisik Konfirmasi Diterima "
                        f"({'Cetak' if spec['is_print'] else 'Polos'})"
                    ),
                    order_id=order.get("id"),
                    line_idx=spec["idx"],
                    line_id=spec.get("line_id"),
                    action_id=action_id,
                    is_print=spec["is_print"],
                    production_process=spec.get("proc"),
                    user=user,
                    fence_generation=None if not fence else fence["generation"],
                    fence_holder_id=None if not fence else fence.get("holder_id"),
                )
                claims.append(claim)
                if fence and fence.get("holder_id") and claim.get("status") == "CLAIMED" and not claim.get("resumed"):
                    await _note_delivery_master(db, fence.get("holder_id"), mid)
            else:
                reserved = await _reserve_delivery_master_group(
                    db, order, action_id, enriched, user,
                )
                if reserved:
                    claims.append(reserved)
        return claims
    except Exception:
        await release_delivery_confirmed_stock(db, claims)
        raise


async def release_delivery_confirmed_stock(
    db,
    claims: List[Dict[str, Any]],
) -> None:
    """Release claims this attempt created. A resumed claim still belongs to the earlier attempt."""
    for c in claims:
        if c and c.get("status") == "CLAIMED" and not c.get("resumed"):
            data = c.get("claim_data") if isinstance(c.get("claim_data"), dict) else {}
            if "fence_generation" in data and str(data.get("reference_type") or "delivery_confirmed") == "delivery_confirmed":
                await _cas_release_delivery_claim(
                    db,
                    c["master_item_id"],
                    c["event_id"],
                    action_id=str(data.get("action_id") or ""),
                    generation=int(data["fence_generation"]),
                    stage="CLAIMED",
                )
            else:
                await release_durable_stock_claim(
                    db,
                    event_id=c["event_id"],
                    master_item_id=c["master_item_id"],
                )


SJ_ROW_UNCERTAIN = (
    "Surat Jalan tidak memiliki rincian baris yang cukup untuk menentukan master dan qty. "
    "Konfirmasi Terkirim tidak mengurangi stok dengan qty penuh PO."
)
SJ_INACTIVE = "Surat Jalan batal atau tidak aktif tidak mengurangi stok."
SJ_QTY_DRIFT = (
    "Qty Surat Jalan berubah sebelum konfirmasi disimpan. "
    "Konfirmasi dan pengurangan stok dibatalkan."
)
SJ_STOCK_SHORT = (
    "Stok fisik tidak mencukupi untuk qty Surat Jalan yang dikonfirmasi. "
    "Konfirmasi disimpan tanpa pengurangan stok pada baris yang kurang."
)
def _delivery_action_recorded(order: Dict[str, Any], action_id: Optional[str]) -> bool:
    token = str(action_id or "").strip()
    if not token:
        return False
    if token in list(order.get("delivery_confirm_action_ids") or []):
        return True
    for rec in order.get("delivery_confirmations") or []:
        if isinstance(rec, dict) and str(rec.get("action_id") or "").strip() == token:
            return True
    return False


DELIVERY_CONFIRM_SCOPE = "delivery_confirmed"
DELIVERY_OPERATION_COLLECTION = "delivery_confirm_operations"
ACTION_REUSED = "action_id sudah dipakai untuk Konfirmasi Terkirim yang berbeda."
OMITTED_ACTION_REQUIRES_SHIPMENT = (
    "Konfirmasi tanpa action_id membutuhkan identitas Surat Jalan yang stabil."
)


def _delivery_operation_db(db):
    return db[DELIVERY_OPERATION_COLLECTION]


def _omit_operation_id(order_id: str, shipment_id: str) -> str:
    return f"omit:{order_id}:{shipment_id}:{DELIVERY_CONFIRM_SCOPE}"


def _explicit_operation_id(action_id: str) -> str:
    return f"act:{action_id}:{DELIVERY_CONFIRM_SCOPE}"


def _line_qty_key(rows) -> tuple:
    found = []
    for ln in rows or []:
        if not isinstance(ln, dict):
            continue
        try:
            idx = int(ln.get("line_idx"))
        except (TypeError, ValueError):
            continue
        raw = ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")
        try:
            qty = int(raw)
        except (TypeError, ValueError):
            continue
        if qty <= 0:
            continue
        found.append((idx, qty))
    return tuple(sorted(found))


def reject_reused_delivery_action(order: Dict[str, Any], action_id: Optional[str], canonical_sid: Optional[str]) -> None:
    """Refuse to treat an action_id already stored for another SJ or payload as success."""
    token = str(action_id or "").strip()
    if not token or not isinstance(order, dict):
        return
    sid = str(canonical_sid or "").strip()
    from quantity_rules import find_shipment

    for rec in order.get("delivery_confirmations") or []:
        if not isinstance(rec, dict) or str(rec.get("action_id") or "").strip() != token:
            continue
        bound = str(rec.get("shipment_id") or "").strip()
        if sid and bound and bound != sid:
            raise HTTPException(409, ACTION_REUSED)
        if not sid or not bound or bound != sid:
            continue
        current = find_shipment(order, sid)
        current_lines = (current or {}).get("lines") if isinstance(current, dict) else None
        stored = _line_qty_key(rec.get("items"))
        live = _line_qty_key(current_lines)
        if stored and live and stored != live:
            raise HTTPException(409, ACTION_REUSED)


def _binding_conflicts(existing: Optional[Dict[str, Any]], order_id: str, shipment_id: str) -> bool:
    if not existing:
        return False
    bound_order = str(existing.get("order_id") or "")
    bound_shipment = str(existing.get("shipment_id") or "")
    if bound_order and bound_order != order_id:
        return True
    if shipment_id and bound_shipment and bound_shipment != shipment_id:
        return True
    return False


async def _ensure_action_binding(db, order_id: str, shipment_id: str, action_id: str) -> str:
    doc_id = _explicit_operation_id(action_id)
    coll = _delivery_operation_db(db)
    try:
        await coll.insert_one({
            "_id": doc_id,
            "kind": "act",
            "order_id": order_id,
            "shipment_id": shipment_id,
            "scope": DELIVERY_CONFIRM_SCOPE,
            "action_id": action_id,
            "holders": 0,
            "fence_protocol": 1,
            "claimed_masters": [],
        })
    except DuplicateKeyError:
        existing = await coll.find_one({"_id": doc_id})
        if _binding_conflicts(existing, order_id, shipment_id):
            raise HTTPException(409, ACTION_REUSED)
    return doc_id


async def _acquire_omitted_action(db, order_id: str, shipment_id: str) -> str:
    """One persistent action id per order and canonical shipment. Concurrent callers share it."""
    coll = _delivery_operation_db(db)
    doc_id = _omit_operation_id(order_id, shipment_id)
    minted = str(uuid.uuid4())
    try:
        await coll.insert_one({
            "_id": doc_id,
            "kind": "omit",
            "order_id": order_id,
            "shipment_id": shipment_id,
            "scope": DELIVERY_CONFIRM_SCOPE,
            "action_id": minted,
            "holders": 0,
            "fence_protocol": 1,
            "claimed_masters": [],
        })
        action_id = minted
    except DuplicateKeyError:
        existing = await coll.find_one({"_id": doc_id})
        action_id = str((existing or {}).get("action_id") or "").strip()
        if not action_id:
            raise HTTPException(409, "Identitas operasi Konfirmasi Terkirim tidak dapat dipulihkan.")
    await _ensure_action_binding(db, order_id, shipment_id, action_id)
    return action_id


async def _reject_explicit_action_conflict(db, order: Dict[str, Any], action_id: str, shipment_id: str) -> None:
    reject_reused_delivery_action(order, action_id, shipment_id)
    other = await db.orders.find_one(
        {
            "id": {"$ne": order.get("id")},
            "$or": [
                {"delivery_confirmations.action_id": action_id},
                {"delivery_confirm_action_ids": action_id},
            ],
        },
        {"_id": 1},
    )
    if other:
        raise HTTPException(409, ACTION_REUSED)


DELIVERY_FENCE_LEASE = timedelta(seconds=30)
DELIVERY_FENCE_ACTIVE = (
    "Stok fisik tidak mencukupi atau terjadi konflik konkurensi. "
    "Konfirmasi Terkirim masih dimiliki percobaan yang aktif."
)
DELIVERY_FENCE_LOST = (
    "Percobaan Konfirmasi Terkirim kehilangan hak sebelum konfirmasi disimpan."
)
DELIVERY_FENCE_REVIEW = (
    "Stok fisik tidak mencukupi atau terjadi konflik konkurensi. "
    "Pemulihan Konfirmasi Terkirim membutuhkan tinjauan manual: "
    "kepemilikan operasi tidak dapat dipastikan."
)
_COMMIT_LOCKED = "COMMIT_LOCKED"


def delivery_fence_now() -> datetime:
    """Clock for delivery-confirm leases. Tests replace this; expiry is not death."""
    return datetime.now(timezone.utc)


def _fence_slot_key(holder_id: str) -> str:
    return hashlib.sha256(str(holder_id).encode()).hexdigest()[:24]


def _as_utc(value) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _token_is_live(token: Any, now: datetime) -> bool:
    if not isinstance(token, dict):
        return False
    lease = _as_utc(token.get("lease_until"))
    current = _as_utc(now) or delivery_fence_now()
    return bool(lease and lease > current)


def _live_tokens(op_fence: Dict[str, Any], now: datetime) -> List[Dict[str, Any]]:
    return [tok for tok in (op_fence.get("tokens") or []) if _token_is_live(tok, now)]


def _fence_path(slot: str) -> str:
    return f"delivery_confirm_fence.ops.{slot}"


async def _operation_is_legacy(db, holder_id: str) -> bool:
    doc = await _delivery_operation_db(db).find_one({"_id": holder_id}, {"fence_protocol": 1})
    return bool(doc) and doc.get("fence_protocol") != 1


async def _read_fence_slot(db, order_id: str, slot: str) -> Optional[Dict[str, Any]]:
    doc = await db.orders.find_one({"id": order_id}, {"delivery_confirm_fence": 1})
    ops = ((doc or {}).get("delivery_confirm_fence") or {}).get("ops") or {}
    found = ops.get(slot)
    return found if isinstance(found, dict) else None


async def _active_fence_for_action(db, order_id: str, action_id: str):
    doc = await db.orders.find_one({"id": order_id}, {"delivery_confirm_fence": 1})
    ops = ((doc or {}).get("delivery_confirm_fence") or {}).get("ops") or {}
    for slot, op in ops.items():
        if isinstance(op, dict) and str(op.get("action_id") or "") == str(action_id or "") and op.get("status") == "active":
            return slot, op
    return None, None


async def _acquire_delivery_attempt(db, order_id: str, holder_id: str, action_id: str, shipment_id: str) -> Dict[str, Any]:
    """One attempt token on this operation. Same-action callers join; they do not steal."""
    if await _operation_is_legacy(db, holder_id):
        return {"fence_protocol": 0, "attempt_token": None, "generation": None, "fence_slot": None}
    slot = _fence_slot_key(holder_id)
    path = _fence_path(slot)
    now = delivery_fence_now()
    token = str(uuid.uuid4())
    entry = {"token": token, "lease_until": now + DELIVERY_FENCE_LEASE}
    created = await db.orders.find_one_and_update(
        {"id": order_id, path: {"$exists": False}},
        {"$set": {
            "delivery_confirm_fence.protocol": 1,
            path: {
                "holder_id": holder_id,
                "action_id": action_id,
                "shipment_id": shipment_id,
                "order_id": order_id,
                "generation": 1,
                "status": "active",
                "tokens": [entry],
            },
        }},
        return_document=ReturnDocument.AFTER,
    )
    created_ops = ((created or {}).get("delivery_confirm_fence") or {}).get("ops") or {}
    created_slot = created_ops.get(slot) if isinstance(created_ops, dict) else None
    if any(isinstance(tok, dict) and tok.get("token") == token for tok in (created_slot or {}).get("tokens") or []):
        return {
            "fence_protocol": 1,
            "attempt_token": token,
            "generation": 1,
            "fence_slot": slot,
        }
    current = await _read_fence_slot(db, order_id, slot)
    if not current or str(current.get("action_id") or "") != action_id:
        raise HTTPException(409, DELIVERY_FENCE_REVIEW)
    if current.get("status") == "recovering" and not _live_tokens(current, delivery_fence_now()):
        await _resume_interrupted_delivery_recovery(db, order_id, holder_id)
        current = await _read_fence_slot(db, order_id, slot)
        if not current or str(current.get("action_id") or "") != action_id:
            raise HTTPException(409, DELIVERY_FENCE_REVIEW)
    if current.get("status") != "active":
        raise HTTPException(409, DELIVERY_FENCE_ACTIVE)
    generation = int(current.get("generation") or 0)
    joined = await db.orders.find_one_and_update(
        {
            "id": order_id,
            f"{path}.status": "active",
            f"{path}.generation": generation,
            f"{path}.action_id": action_id,
        },
        {"$push": {f"{path}.tokens": entry}},
        return_document=ReturnDocument.AFTER,
    )
    joined_ops = ((joined or {}).get("delivery_confirm_fence") or {}).get("ops") or {}
    joined_slot = joined_ops.get(slot) if isinstance(joined_ops, dict) else None
    if not any(isinstance(tok, dict) and tok.get("token") == token for tok in (joined_slot or {}).get("tokens") or []):
        raise HTTPException(409, DELIVERY_FENCE_ACTIVE)
    return {
        "fence_protocol": 1,
        "attempt_token": token,
        "generation": generation,
        "fence_slot": slot,
    }


async def _renew_delivery_attempt(db, op: Dict[str, Any]) -> bool:
    if not op or op.get("fence_protocol") != 1:
        return True
    slot = op.get("fence_slot")
    token = op.get("attempt_token")
    order_id = op.get("order_id")
    generation = op.get("generation")
    if not slot or not token or not order_id or generation is None:
        return False
    path = _fence_path(slot)
    res = await db.orders.update_one(
        {
            "id": order_id,
            f"{path}.status": "active",
            f"{path}.generation": int(generation),
            f"{path}.action_id": op.get("action_id"),
            f"{path}.tokens.token": token,
        },
        {"$set": {f"{path}.tokens.$.lease_until": delivery_fence_now() + DELIVERY_FENCE_LEASE}},
    )
    return res.matched_count == 1


async def _pull_attempt_token(db, op: Optional[Dict[str, Any]]) -> None:
    if not op or op.get("fence_protocol") != 1:
        return
    slot = op.get("fence_slot")
    token = op.get("attempt_token")
    order_id = op.get("order_id")
    if not slot or not token or not order_id:
        return
    path = _fence_path(slot)
    await db.orders.update_one(
        {"id": order_id},
        {"$pull": {f"{path}.tokens": {"token": token}}},
    )


def delivery_persist_filter(op: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Predicates that must succeed in the same update as the confirmation write."""
    if not op or op.get("fence_protocol") != 1:
        return None
    slot = op.get("fence_slot")
    token = op.get("attempt_token")
    if not slot or not token or op.get("generation") is None:
        return None
    path = _fence_path(slot)
    return {
        f"{path}.status": "active",
        f"{path}.generation": int(op["generation"]),
        f"{path}.action_id": op.get("action_id"),
        f"{path}.tokens.token": token,
    }


async def _seal_delivery_claim(db, claim: Dict[str, Any], generation: int) -> bool:
    mid = str(claim.get("master_item_id") or "").strip()
    ev_id = str(claim.get("event_id") or "").strip()
    if not mid or not ev_id:
        return False
    res = await db[STATE_COLLECTION].update_one(
        {
            "$or": [{"_id": mid}, {"master_item_id": mid}],
            "pending_event.event_id": ev_id,
            "pending_event.reference_type": "delivery_confirmed",
            "pending_event.stage": "CLAIMED",
            "pending_event.fence_generation": int(generation),
        },
        {"$set": {"pending_event.stage": _COMMIT_LOCKED}},
    )
    if res.matched_count:
        return True
    curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
    pending = (curr or {}).get("pending_event") if curr else None
    if not isinstance(pending, dict):
        return True
    if str(pending.get("event_id") or "") != ev_id:
        return False
    if "fence_generation" not in pending:
        return True
    if int(pending.get("fence_generation") or -1) != int(generation):
        return False
    return pending.get("stage") in (_COMMIT_LOCKED, "STATE_APPLIED")


async def prepare_delivery_commit(db, op: Optional[Dict[str, Any]], claims: Optional[List[Dict[str, Any]]]) -> None:
    """Renew and seal before the confirmation write. A failed seal does not persist."""
    if not op or op.get("fence_protocol") != 1:
        return
    if not await _renew_delivery_attempt(db, op):
        raise HTTPException(409, DELIVERY_FENCE_LOST)
    generation = int(op["generation"])
    for claim in claims or []:
        if not claim or claim.get("status") != "CLAIMED":
            continue
        if not await _seal_delivery_claim(db, claim, generation):
            raise HTTPException(409, DELIVERY_FENCE_LOST)


async def _note_delivery_master(db, holder_id: Optional[str], master_item_id: str) -> None:
    if not holder_id or not master_item_id:
        return
    await _delivery_operation_db(db).update_one(
        {"_id": holder_id, "fence_protocol": 1},
        {"$addToSet": {"claimed_masters": str(master_item_id)}},
    )


async def _fence_stamp_for_action(db, order_id: str, action_id: str):
    slot, op = await _active_fence_for_action(db, order_id, action_id)
    if not op:
        return None
    try:
        generation = int(op.get("generation"))
    except (TypeError, ValueError):
        return None
    return {
        "generation": generation,
        "holder_id": op.get("holder_id"),
        "slot": slot,
    }


async def _cas_release_delivery_claim(
    db,
    master_item_id: str,
    event_id: str,
    *,
    action_id: str,
    generation: int,
    stage: str,
) -> int:
    """Drop a delivery reservation only while stage and generation still match.

    The update never changes bucket balances. A stale CLAIMED read cannot reverse
    a reservation that has already become STATE_APPLIED.
    """
    res = await db[STATE_COLLECTION].update_one(
        {
            "$or": [{"_id": master_item_id}, {"master_item_id": master_item_id}],
            "pending_event.event_id": event_id,
            "pending_event.reference_type": "delivery_confirmed",
            "pending_event.action_id": action_id,
            "pending_event.stage": stage,
            "pending_event.fence_generation": int(generation),
        },
        {"$unset": {"pending_event": ""}},
    )
    return int(res.matched_count or 0)


async def _cas_fence_takeover(db, order_id: str, slot: str, generation: int, now: datetime) -> Optional[str]:
    path = _fence_path(slot)
    recovery_token = str(uuid.uuid4())
    res = await db.orders.update_one(
        {
            "id": order_id,
            f"{path}.generation": int(generation),
            f"{path}.status": {"$in": ["active", "recovering"]},
            f"{path}.tokens": {"$not": {"$elemMatch": {"lease_until": {"$gt": now}}}},
        },
        {"$set": {
            f"{path}.generation": int(generation) + 1,
            f"{path}.status": "recovering",
            f"{path}.fenced_generation": int(generation),
            f"{path}.tokens": [{
                "token": recovery_token,
                "lease_until": now + DELIVERY_FENCE_LEASE,
                "role": "recovery",
            }],
        }},
    )
    if res.modified_count != 1:
        return None
    return recovery_token


async def _finish_fence_recovery(db, order_id: str, slot: str, generation: int, recovery_token: str) -> None:
    path = _fence_path(slot)
    await db.orders.update_one(
        {
            "id": order_id,
            f"{path}.generation": int(generation),
            f"{path}.status": "recovering",
            f"{path}.tokens.token": recovery_token,
        },
        {"$set": {
            f"{path}.status": "active",
            f"{path}.tokens": [],
        }},
    )


async def _drop_recovery_lease(db, order_id: str, slot: str, generation: int, recovery_token: str) -> None:
    """End this recovery's lease without opening the slot or touching reservations."""
    path = _fence_path(slot)
    await db.orders.update_one(
        {
            "id": order_id,
            f"{path}.generation": int(generation),
            f"{path}.status": "recovering",
            f"{path}.tokens.token": recovery_token,
        },
        {"$set": {f"{path}.tokens.$.lease_until": delivery_fence_now()}},
    )


async def _close_idle_recovery_slot(db, order_id: str, holder_id: str) -> None:
    """Mark a recovering slot active only when it has no live token and nothing left to settle."""
    slot = _fence_slot_key(holder_id)
    op_fence = await _read_fence_slot(db, order_id, slot)
    if not op_fence or op_fence.get("status") != "recovering":
        return
    now = delivery_fence_now()
    if _live_tokens(op_fence, now):
        return
    try:
        generation = int(op_fence.get("generation"))
    except (TypeError, ValueError):
        return
    path = _fence_path(slot)
    await db.orders.update_one(
        {
            "id": order_id,
            f"{path}.status": "recovering",
            f"{path}.generation": generation,
            f"{path}.tokens": {"$not": {"$elemMatch": {"lease_until": {"$gt": now}}}},
        },
        {"$set": {f"{path}.status": "active", f"{path}.tokens": []}},
    )


async def _bump_pending_fence(db, master_item_id: str, event_id: str, action_id: str, old_gen: int, new_gen: int) -> bool:
    res = await db[STATE_COLLECTION].update_one(
        {
            "$or": [{"_id": master_item_id}, {"master_item_id": master_item_id}],
            "pending_event.event_id": event_id,
            "pending_event.reference_type": "delivery_confirmed",
            "pending_event.action_id": action_id,
            "pending_event.fence_generation": int(old_gen),
        },
        {"$set": {"pending_event.fence_generation": int(new_gen)}},
    )
    return res.matched_count == 1


def _pending_fence_generation(pending: Dict[str, Any]) -> Optional[int]:
    if "fence_generation" not in pending:
        return None
    try:
        return int(pending.get("fence_generation"))
    except (TypeError, ValueError):
        return None


async def _finalize_fenced_delivery_pending(db, pending: Dict[str, Any], generation: int, user=None):
    return await finalize_durable_stock_event(
        db,
        event_id=str(pending.get("event_id") or ""),
        master_item_id=str(pending.get("master_item_id") or ""),
        user=user,
        expected_fence_generation=int(generation),
    )


async def _read_delivery_pending(db, master_item_id: str):
    curr = await db[STATE_COLLECTION].find_one(
        {"$or": [{"_id": master_item_id}, {"master_item_id": master_item_id}]}
    )
    pending = (curr or {}).get("pending_event") if curr else None
    return pending if isinstance(pending, dict) else None


async def _adopt_pending_generation(db, master_item_id: str, pending: Dict[str, Any], action_id: str, new_gen: int):
    """CAS this reservation onto the recovery generation.

    Returns ('owned', pending), ('gone', None) when the reservation disappeared,
    or ('review', None) when ownership cannot be proved.
    Generations older than new_gen are the crash window after order takeover.
    """
    observed = _pending_fence_generation(pending)
    if observed is None or observed > int(new_gen):
        return "review", None
    event_id = str(pending.get("event_id") or "")
    if observed != int(new_gen):
        await _bump_pending_fence(db, master_item_id, event_id, action_id, observed, new_gen)
    fresh = await _read_delivery_pending(db, master_item_id)
    if not fresh or str(fresh.get("event_id") or "") != event_id:
        return "gone", None
    if str(fresh.get("action_id") or "") != action_id:
        return "review", None
    if _pending_fence_generation(fresh) != int(new_gen):
        return "review", None
    return "owned", fresh


async def _settle_owned_pending(db, master_item_id: str, pending: Dict[str, Any], action_id: str, new_gen: int, user=None) -> str:
    """Finalize a committed or applied reservation, or CAS-release an uncommitted one.

    Source is read after the order takeover, and again immediately before release.
    A commit that won before takeover is visible. A commit cannot land afterwards
    while the slot stays recovering. The release filter still requires the stage
    that was observed, so a stale CLAIMED snapshot cannot unset STATE_APPLIED.
    """
    stage = pending.get("stage") or "CLAIMED"
    committed = await is_source_committed(db, pending)
    if stage == "STATE_APPLIED":
        if not committed:
            return "review"
        await _finalize_fenced_delivery_pending(db, pending, new_gen, user)
        return "done"
    if committed:
        await _finalize_fenced_delivery_pending(db, pending, new_gen, user)
        return "done"
    if stage not in ("CLAIMED", _COMMIT_LOCKED):
        return "review"
    if await is_source_committed(db, pending):
        await _finalize_fenced_delivery_pending(db, pending, new_gen, user)
        return "done"
    matched = await _cas_release_delivery_claim(
        db, master_item_id, str(pending.get("event_id") or ""),
        action_id=action_id, generation=new_gen, stage=stage,
    )
    if matched:
        return "done"
    fresh = await _read_delivery_pending(db, master_item_id)
    if not fresh or str(fresh.get("event_id") or "") != str(pending.get("event_id") or ""):
        return "done"
    if str(fresh.get("action_id") or "") != action_id or _pending_fence_generation(fresh) != int(new_gen):
        return "done"
    if fresh.get("stage") == "STATE_APPLIED":
        if await is_source_committed(db, fresh):
            await _finalize_fenced_delivery_pending(db, fresh, new_gen, user)
            return "done"
        return "review"
    if await is_source_committed(db, fresh):
        await _finalize_fenced_delivery_pending(db, fresh, new_gen, user)
        return "done"
    return "review"


async def _recover_operation_masters(db, holder: Dict[str, Any], origin_mid: str, old_gen: int, new_gen: int, user=None) -> str:
    """Fence and settle every master this operation recorded. Other operations stay put."""
    order_id = str(holder.get("order_id") or "")
    action_id = str(holder.get("action_id") or "")
    masters = [str(mid) for mid in (holder.get("claimed_masters") or []) if str(mid or "").strip()]
    if origin_mid not in masters:
        masters.append(origin_mid)
    # old_gen is the generation this takeover superseded. Pending rows may still
    # sit on that generation or on an older one if a previous takeover crashed.
    if int(old_gen) >= int(new_gen):
        return "MANUAL_REVIEW"
    for mid in masters:
        pending = await _read_delivery_pending(db, mid)
        if not pending:
            continue
        if str(pending.get("reference_type") or "") != "delivery_confirmed":
            continue
        if str(pending.get("action_id") or "") != action_id:
            continue
        if str(pending.get("order_id") or "") != order_id:
            continue
        adopted, owned = await _adopt_pending_generation(db, mid, pending, action_id, new_gen)
        if adopted == "gone":
            continue
        if adopted != "owned" or not owned:
            return "MANUAL_REVIEW"
        if await _settle_owned_pending(db, mid, owned, action_id, new_gen, user) != "done":
            return "MANUAL_REVIEW"
    return "RECOVERED"


async def _continue_delivery_recovery(db, master_item_id: str, observed: Dict[str, Any], user=None) -> Dict[str, Any]:
    """Resume recovery after the initial read. Mutations re-check stage and lease."""
    mid = str(master_item_id or "").strip()
    curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
    pending = (curr or {}).get("pending_event") if curr else None
    if not isinstance(pending, dict) or not pending.get("event_id"):
        return {"status": "CLEAR"}
    if str(pending.get("reference_type") or "") != "delivery_confirmed":
        return {"status": "FOREIGN", "reference_type": pending.get("reference_type")}
    if str(pending.get("event_id") or "") != str(observed.get("event_id") or ""):
        return {"status": "CHANGED"}
    if _pending_fence_generation(pending) is None:
        return {"status": "MANUAL_REVIEW", "reason": DELIVERY_FENCE_REVIEW}
    action_id = str(pending.get("action_id") or "").strip()
    order_id = str(pending.get("order_id") or "").strip()
    holder_id = str(pending.get("fence_holder_id") or "").strip()
    if not action_id or not order_id:
        return {"status": "MANUAL_REVIEW", "reason": DELIVERY_FENCE_REVIEW}
    docs = await _delivery_operation_db(db).find(
        {"action_id": action_id, "order_id": order_id, "scope": DELIVERY_CONFIRM_SCOPE},
    ).to_list(length=5)
    if holder_id:
        direct = await _delivery_operation_db(db).find_one({"_id": holder_id})
        if direct and all(str(doc.get("_id")) != holder_id for doc in docs):
            docs.append(direct)
    protocol = [doc for doc in docs if doc.get("fence_protocol") == 1]
    if not protocol:
        return {"status": "MANUAL_REVIEW", "reason": DELIVERY_FENCE_REVIEW}
    holder = next((doc for doc in protocol if str(doc.get("_id")) == holder_id), protocol[0])
    claimed = []
    for doc in protocol:
        for item in doc.get("claimed_masters") or []:
            if str(item) not in claimed:
                claimed.append(str(item))
    holder = dict(holder)
    holder["claimed_masters"] = claimed
    holder_id = str(holder.get("_id") or holder_id)
    slot = _fence_slot_key(holder_id)
    op_fence = await _read_fence_slot(db, order_id, slot)
    if not op_fence or str(op_fence.get("action_id") or "") != action_id:
        return {"status": "MANUAL_REVIEW", "reason": DELIVERY_FENCE_REVIEW}
    now = delivery_fence_now()
    if _live_tokens(op_fence, now):
        return {"status": "ACTIVE"}
    try:
        generation = int(op_fence.get("generation"))
    except (TypeError, ValueError):
        return {"status": "MANUAL_REVIEW", "reason": DELIVERY_FENCE_REVIEW}
    pending_gen = _pending_fence_generation(pending)
    if pending_gen is None or pending_gen > int(generation):
        return {"status": "MANUAL_REVIEW", "reason": DELIVERY_FENCE_REVIEW}
    recovery_token = await _cas_fence_takeover(db, order_id, slot, generation, now)
    if not recovery_token:
        again = await _read_fence_slot(db, order_id, slot)
        if again and _live_tokens(again, delivery_fence_now()):
            return {"status": "ACTIVE"}
        return {"status": "LOST_RACE"}
    try:
        outcome = await _recover_operation_masters(
            db, holder, mid, generation, generation + 1, user,
        )
    except Exception:
        await _drop_recovery_lease(db, order_id, slot, generation + 1, recovery_token)
        raise
    if outcome != "RECOVERED":
        await _drop_recovery_lease(db, order_id, slot, generation + 1, recovery_token)
        return {"status": "MANUAL_REVIEW", "reason": DELIVERY_FENCE_REVIEW}
    await _finish_fence_recovery(db, order_id, slot, generation + 1, recovery_token)
    return {"status": outcome}


async def recover_abandoned_delivery_pending(db, master_item_id: str, user=None) -> Dict[str, Any]:
    """On-demand recovery for one master. A read is not permission to delete."""
    mid = str(master_item_id or "").strip()
    curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
    pending = (curr or {}).get("pending_event") if curr else None
    if not isinstance(pending, dict) or not pending.get("event_id"):
        return {"status": "CLEAR"}
    if str(pending.get("reference_type") or "") != "delivery_confirmed":
        return {"status": "FOREIGN", "reference_type": pending.get("reference_type")}
    return await _continue_delivery_recovery(db, mid, pending, user)


async def _resume_interrupted_delivery_recovery(db, order_id: str, holder_id: str) -> None:
    """Continue a recovering slot whose lease is already eligible. A live lease is not resumed here."""
    holder = await _delivery_operation_db(db).find_one({"_id": holder_id}, {"claimed_masters": 1})
    masters = [str(mid) for mid in (holder or {}).get("claimed_masters") or [] if str(mid or "").strip()]
    if not masters:
        raise HTTPException(409, DELIVERY_FENCE_REVIEW)
    outcome = await recover_abandoned_delivery_pending(db, masters[0])
    status = str((outcome or {}).get("status") or "")
    if status in {"ACTIVE", "LOST_RACE"}:
        raise HTTPException(409, DELIVERY_FENCE_ACTIVE)
    if status == "MANUAL_REVIEW":
        raise HTTPException(409, (outcome or {}).get("reason") or DELIVERY_FENCE_REVIEW)
    if status == "FOREIGN":
        raise HTTPException(409, DELIVERY_FENCE_REVIEW)
    if status == "CLEAR":
        await _close_idle_recovery_slot(db, order_id, holder_id)


async def _recover_needed_delivery_masters(db, order: Dict[str, Any], lines: List[Dict[str, Any]], action_id: str) -> None:
    seen = set()
    order_id = str(order.get("id") or "")
    for spec in _delivery_claim_specs(order, lines, action_id):
        mid = spec["mid"]
        if mid in seen:
            continue
        seen.add(mid)
        curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
        pending = (curr or {}).get("pending_event") if curr else None
        if not isinstance(pending, dict):
            continue
        if str(pending.get("reference_type") or "") != "delivery_confirmed":
            continue
        same_action = str(pending.get("action_id") or "") == str(action_id or "")
        same_order = str(pending.get("order_id") or "") == order_id
        if same_action and same_order:
            continue
        result = await recover_abandoned_delivery_pending(db, mid)
        status = result.get("status")
        if status == "ACTIVE":
            raise HTTPException(409, DELIVERY_FENCE_ACTIVE)
        if status == "MANUAL_REVIEW":
            raise HTTPException(409, result.get("reason") or DELIVERY_FENCE_REVIEW)
        if status == "LOST_RACE":
            raise HTTPException(409, DELIVERY_FENCE_ACTIVE)


async def prepare_confirm_delivered_action(db, order, selected_shipment_id, client_action_id) -> Dict[str, Any]:
    """Resolve the operation id before any stock mutation and hold it for this request.

    The id lives outside the order document. Confirm persists the order with a
    full replacement, which would drop an id stored only on that document.
    """
    if not order:
        return {
            "action_id": client_action_id,
            "shipment_id": None,
            "holder_id": None,
            "ready": False,
            "order": None,
        }
    from quantity_rules import canonical_shipment_id, resolve_delivery_shipment

    shipment, err = resolve_delivery_shipment(order, selected_shipment_id)
    if err:
        raise HTTPException(400, err)
    sid = canonical_shipment_id(shipment) if isinstance(shipment, dict) else ""
    order_id = str(order.get("id") or "")
    client_token = str(client_action_id or "").strip()
    if client_token:
        await _reject_explicit_action_conflict(db, order, client_token, sid)
        holder_id = await _ensure_action_binding(db, order_id, sid, client_token)
        action_id = client_token
    else:
        if not sid:
            raise HTTPException(400, OMITTED_ACTION_REQUIRES_SHIPMENT)
        action_id = await _acquire_omitted_action(db, order_id, sid)
        holder_id = _omit_operation_id(order_id, sid)
    attempt = await _acquire_delivery_attempt(db, order_id, holder_id, action_id, sid)
    await _delivery_operation_db(db).update_one({"_id": holder_id}, {"$inc": {"holders": 1}})
    fresh = await db.orders.find_one({"id": order_id}, {"_id": 0})
    return {
        "action_id": action_id,
        "shipment_id": sid,
        "holder_id": holder_id,
        "ready": True,
        "order": fresh or order,
        "order_id": order_id,
        "attempt_token": attempt.get("attempt_token"),
        "generation": attempt.get("generation"),
        "fence_protocol": attempt.get("fence_protocol") or 0,
        "fence_slot": attempt.get("fence_slot"),
    }


async def end_delivery_operation(db, holder_id: Optional[str]) -> int:
    if not holder_id:
        return 0
    updated = await _delivery_operation_db(db).find_one_and_update(
        {"_id": holder_id, "holders": {"$gt": 0}},
        {"$inc": {"holders": -1}},
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        return 0
    return int(updated.get("holders") or 0)


async def release_uncommitted_delivery_claim(
    db,
    claims: Optional[List[Dict[str, Any]]],
    order_id: str,
    action_id: Optional[str],
    holders_remaining: int,
    holder_id: Optional[str] = None,
    expected_generation: Optional[int] = None,
) -> None:
    """Release this action's uncommitted claim when no sibling still holds it.

    A claim this request only resumed is still releasable here. The holder
    count, a stored confirmation, and a pending stage other than CLAIMED keep
    a live or applied reservation. Claim-time release does not see that holder
    count, so it releases only a claim this attempt created.
    """
    if holders_remaining > 0:
        return
    if holder_id:
        current = await _delivery_operation_db(db).find_one({"_id": holder_id}, {"holders": 1})
        if current and int(current.get("holders") or 0) > 0:
            return
    fresh = await db.orders.find_one(
        {"id": order_id},
        {"_id": 0, "delivery_confirm_action_ids": 1, "delivery_confirmations": 1},
    )
    if _delivery_action_recorded(fresh or {}, action_id):
        return
    token = str(action_id or "").strip()
    for claim in claims or []:
        if not claim or claim.get("status") != "CLAIMED":
            continue
        mid = str(claim.get("master_item_id") or "").strip()
        ev_id = str(claim.get("event_id") or "").strip()
        if not mid or not ev_id:
            continue
        curr = await db[STATE_COLLECTION].find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
        pending = (curr or {}).get("pending_event") if curr else None
        if not isinstance(pending, dict):
            continue
        if str(pending.get("event_id") or "") != ev_id:
            continue
        if token and str(pending.get("action_id") or "") != token:
            continue
        if "fence_generation" in pending:
            try:
                pending_gen = int(pending.get("fence_generation"))
            except (TypeError, ValueError):
                continue
            if expected_generation is not None and pending_gen != int(expected_generation):
                continue
            if pending.get("stage") == "CLAIMED":
                await _cas_release_delivery_claim(
                    db, mid, ev_id, action_id=token or str(pending.get("action_id") or ""),
                    generation=pending_gen, stage="CLAIMED",
                )
            elif pending.get("stage") == _COMMIT_LOCKED:
                await _cas_release_delivery_claim(
                    db, mid, ev_id, action_id=token or str(pending.get("action_id") or ""),
                    generation=pending_gen, stage=_COMMIT_LOCKED,
                )
            continue
        if pending.get("stage") != "CLAIMED":
            continue
        await release_durable_stock_claim(db, event_id=ev_id, master_item_id=mid)


async def finish_confirm_delivered_action(
    db,
    op: Optional[Dict[str, Any]],
    claims: Optional[List[Dict[str, Any]]],
    order_id: str,
    action_id: Optional[str],
    *,
    release: bool,
) -> None:
    holder_id = (op or {}).get("holder_id")
    await _pull_attempt_token(db, op)
    remaining = await end_delivery_operation(db, holder_id)
    if release:
        await release_uncommitted_delivery_claim(
            db, claims, order_id, action_id, remaining, holder_id=holder_id,
            expected_generation=(op or {}).get("generation"),
        )


def _sj_stock_fingerprint(lines: List[Dict[str, Any]]) -> tuple:
    rows = []
    for ln in lines or []:
        rows.append((
            int(ln.get("line_idx")),
            str(ln.get("line_id") or ""),
            str(ln.get("master_item_id") or ""),
            str(ln.get("accurate_item_id") or ""),
            float(ln.get("qty") or 0),
            str(ln.get("production_process") or ""),
            str(ln.get("unit") or ""),
        ))
    return tuple(rows)


def plan_confirmed_sj_stock(
    order: Dict[str, Any],
    shipment: Optional[Dict[str, Any]],
    *,
    allow_delivered: bool = False,
) -> Dict[str, Any]:
    """Map confirmed SJ rows to canonical PO lines. Qty stays on the SJ row.

    Reconciliation is applied on a copy first. The current reconciler does not
    rewrite SJ lines. Identity fields come only from the matched PO line.
    """
    from copy import deepcopy

    from quantity_rules import (
        delivery_batch_identity,
        delivery_batch_qty,
        find_shipment,
        match_po_line_idx,
        reconcile_sj_quantities_to_loaded_delivery,
        shipment_is_active,
        shipment_is_delivered,
    )

    if not isinstance(shipment, dict):
        return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
    probe = deepcopy(order)
    reconcile_sj_quantities_to_loaded_delivery(probe)
    ident = delivery_batch_identity(shipment)
    sh = find_shipment(probe, ident) if ident else None
    if not isinstance(sh, dict):
        sh = shipment if shipment in (probe.get("shipments") or []) else None
    if not isinstance(sh, dict):
        return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
    if not shipment_is_active(sh):
        return {"lines": [], "error": SJ_INACTIVE, "skip": False}
    if shipment_is_delivered(probe, sh) and not allow_delivered:
        return {"lines": [], "error": None, "skip": True}
    raw = [ln for ln in (sh.get("lines") or []) if isinstance(ln, dict)]
    if not raw:
        return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
    lis = list(probe.get("line_items") or [])
    lines: List[Dict[str, Any]] = []
    accounted = 0
    for ln in raw:
        qty_raw = ln.get("qty") if ln.get("qty") is not None else ln.get("quantity")
        try:
            qty = int(qty_raw)
        except (TypeError, ValueError):
            return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
        if qty <= 0:
            continue
        idx, err = match_po_line_idx(probe, ln)
        if err or idx is None or not (0 <= int(idx) < len(lis)):
            return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
        po = lis[int(idx)]
        if not isinstance(po, dict):
            return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
        mid = str(po.get("master_item_id") or "").strip()
        if not mid:
            return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
        lines.append({
            "line_idx": int(idx),
            "qty": qty,
            "quantity": qty,
            "master_item_id": mid,
            "accurate_item_id": po.get("accurate_item_id"),
            "production_process": str(po.get("production_process") or "").strip().lower(),
            "unit": po.get("unit") or "PCS",
            "line_id": po.get("line_id"),
        })
        accounted += qty
    if not lines:
        return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
    if delivery_batch_qty(sh) != accounted:
        return {"lines": [], "error": SJ_ROW_UNCERTAIN, "skip": False}
    return {"lines": lines, "error": None, "skip": False, "sj_qty": accounted}


def reject_sj_stock_qty_drift(order, shipment, holder, confirmation_qty) -> None:
    """Refuse to store a confirmation whose qty is not the reserved SJ qty."""
    if not isinstance(holder, dict) or not holder.get("active"):
        return
    plan = plan_confirmed_sj_stock(order, shipment, allow_delivered=True)
    if plan.get("error") or plan.get("skip"):
        raise HTTPException(400, plan.get("error") or SJ_QTY_DRIFT)
    if _sj_stock_fingerprint(plan.get("lines") or []) != holder.get("fingerprint"):
        raise HTTPException(400, SJ_QTY_DRIFT)
    planned = sum(int(ln.get("qty") or 0) for ln in plan.get("lines") or [])
    try:
        confirmed = int(confirmation_qty)
    except (TypeError, ValueError):
        confirmed = -1
    if confirmed != planned:
        raise HTTPException(400, SJ_QTY_DRIFT)


def _covered_delivery_event_ids(claims: List[Dict[str, Any]]) -> set:
    covered = set()
    for claim in claims or []:
        if not isinstance(claim, dict):
            continue
        covered.add(str(claim.get("event_id") or ""))
        data = claim.get("claim_data") if isinstance(claim.get("claim_data"), dict) else {}
        for row in data.get("line_claims") or []:
            if isinstance(row, dict):
                covered.add(str(row.get("event_id") or ""))
        movements = list(claim.get("line_movements") or [])
        if isinstance(claim.get("movement"), dict):
            movements.append(claim["movement"])
        for mov in movements:
            if not isinstance(mov, dict):
                continue
            covered.add(str(mov.get("event_key") or ""))
            covered.add(str(mov.get("id") or ""))
            covered.add(str(mov.get("source_event_id") or ""))
    covered.discard("")
    return covered


def _reserved_transaction_qty(claims: List[Dict[str, Any]]) -> float:
    total = 0.0
    for claim in claims or []:
        data = claim.get("claim_data") or {}
        rows = data.get("line_claims")
        if isinstance(rows, list) and rows:
            total += sum(safe_float(row.get("transaction_qty")) for row in rows)
        else:
            total += safe_float(data.get("transaction_qty"))
    return total


async def _delivery_line_stock_short(db, line: Dict[str, Any]) -> bool:
    """True when initialized stock cannot cover this SJ line.

    Missing opening stock is not a shortage. Shortage does not reject confirmation.
    """
    mid = str(line.get("master_item_id") or "").strip()
    if not await _product_stock_is_initialized(db, mid):
        return False
    proc = str(line.get("production_process") or "").strip().lower()
    return not await _production_flow_stock_can_mutate(
        db,
        mid,
        line.get("qty"),
        line.get("unit"),
        movement_type=MOVEMENT_PENGIRIMAN,
        is_print=proc != "non_print",
    )


async def _delivery_group_exceeds_stock(db, enriched: List[Dict[str, Any]]) -> bool:
    """True when the summed PENGIRIMAN deltas of one master exceed initialized buckets."""
    if not enriched:
        return False
    mid = str(enriched[0].get("mid") or "").strip()
    if not await _product_stock_is_initialized(db, mid):
        return False
    state = await get_product_stock_state(db, mid)
    if not state:
        return False
    need_printed = sum(abs(safe_float(line.get("delta_printed"))) for line in enriched)
    need_unprinted = sum(abs(safe_float(line.get("delta_unprinted"))) for line in enriched)
    need_total = sum(abs(safe_float(line.get("delta_total"))) for line in enriched)
    if need_printed > safe_float(state.get("printed_qty", 0)) + 0.0001:
        return True
    if need_unprinted > safe_float(state.get("unprinted_qty", 0)) + 0.0001:
        return True
    if need_total > safe_float(state.get("total_qty", 0)) + 0.0001:
        return True
    return False


async def _group_should_skip_for_short_stock(
    db,
    order_id: str,
    action_id: str,
    group: List[Dict[str, Any]],
) -> bool:
    """Skip a new grouped reservation when the sum exceeds stock.

    Existing movements and this action's pending stay on the claim path so a
    retry can resume. Another action's pending also stays on the claim path so
    the concurrency conflict is still raised.
    """
    if not group:
        return False
    for spec in group:
        if await _movement_by_event(db, spec.get("event_id")):
            return False
        if await _pending_covers_delivery_spec(db, order_id, action_id, spec):
            return False
    state = await get_product_stock_state(db, group[0].get("mid"))
    pending = (state or {}).get("pending_event") if state else None
    if isinstance(pending, dict) and pending.get("event_id"):
        return False
    enriched = group if "delta_total" in group[0] else [
        await _delivery_line_deltas(db, spec) for spec in group
    ]
    return await _delivery_group_exceeds_stock(db, enriched)


async def claim_selected_delivery_stock(
    db,
    order: Dict[str, Any],
    shipment_id: Optional[str],
    action_id: str,
    user: Optional[Dict[str, Any]],
    holder: Dict[str, Any],
) -> Optional[List[Dict[str, Any]]]:
    """Claim only the selected SJ rows, identified by their canonical PO lines."""
    holder.clear()
    if not await should_claim_delivery_confirmed_stock(db, order):
        return None
    from quantity_rules import resolve_delivery_shipment, shipment_is_delivered

    shipment, err = resolve_delivery_shipment(order, shipment_id)
    if err:
        raise HTTPException(400, err)
    replay = _delivery_action_recorded(order, action_id)
    if isinstance(shipment, dict) and shipment_is_delivered(order, shipment) and not replay:
        holder["skip"] = True
        return None
    plan = plan_confirmed_sj_stock(order, shipment, allow_delivered=replay)
    if plan.get("skip"):
        holder["skip"] = True
        return None
    if plan.get("error"):
        raise HTTPException(400, plan["error"])
    lines = plan["lines"]
    await _recover_needed_delivery_masters(db, order, lines, action_id)
    order_id = str(order.get("id") or "")
    if not replay:
        for line in lines:
            if await _delivery_line_stock_short(db, line):
                holder["stock_warning"] = True
                break
    specs = _delivery_claim_specs(order, lines, action_id)
    responsible = []
    for spec in specs:
        if await _delivery_spec_is_claimable(
            db, spec, order_id=order_id, action_id=action_id, replay=replay,
        ):
            responsible.append(spec)
    if not replay and responsible:
        grouped_specs: Dict[str, List[Dict[str, Any]]] = {}
        for spec in responsible:
            grouped_specs.setdefault(spec["mid"], []).append(spec)
        kept = []
        for group in grouped_specs.values():
            if len(group) > 1 and await _group_should_skip_for_short_stock(
                db, order_id, action_id, group,
            ):
                holder["stock_warning"] = True
                continue
            kept.extend(group)
        responsible = kept
    if holder.get("stock_warning"):
        logger.warning(
            "%s order=%s action=%s shipment=%s",
            SJ_STOCK_SHORT, order_id, action_id, shipment_id,
        )
    claims = await claim_delivery_confirmed_stock(
        db, order, lines, action_id, user, replay=replay,
    )
    covered = _covered_delivery_event_ids(claims)
    if any(spec["event_id"] not in covered for spec in responsible):
        await release_delivery_confirmed_stock(db, claims)
        raise HTTPException(400, SJ_QTY_DRIFT)
    finalized = bool(claims) and all(c.get("status") == "ALREADY_FINALIZED" for c in claims)
    planned_qty = sum(safe_float(spec.get("qty")) for spec in responsible)
    if not finalized and abs(_reserved_transaction_qty(claims) - planned_qty) > 0.0001:
        await release_delivery_confirmed_stock(db, claims)
        raise HTTPException(400, SJ_QTY_DRIFT)
    holder["active"] = True
    holder["lines"] = lines
    holder["fingerprint"] = _sj_stock_fingerprint(lines)
    return claims


def _delivery_expected_lines(claim: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Line rows this claim must already have as movements once pending is gone."""
    data = claim.get("claim_data") if isinstance(claim.get("claim_data"), dict) else {}
    rows = data.get("line_claims") if isinstance(data.get("line_claims"), list) else None
    if rows:
        return [row for row in rows if isinstance(row, dict) and str(row.get("event_id") or "").strip()]
    event_id = str(claim.get("event_id") or data.get("event_id") or "").strip()
    if not event_id:
        return []
    return [{
        "event_id": event_id,
        "line_id": data.get("line_id"),
        "line_idx": data.get("line_idx"),
        "transaction_qty": data.get("transaction_qty"),
        "delta_printed": data.get("delta_printed"),
        "delta_unprinted": data.get("delta_unprinted"),
        "delta_total": data.get("delta_total"),
    }]


def _delivery_movement_matches_line(mov: Dict[str, Any], spec: Dict[str, Any], *, order_id: str, action_id: str, master_item_id: str) -> bool:
    event_id = str(spec.get("event_id") or "")
    if not event_id or action_id not in event_id or order_id not in event_id:
        return False
    ident = str(mov.get("id") or mov.get("event_key") or "")
    if ident != event_id and str(mov.get("event_key") or "") != event_id:
        return False
    if str(mov.get("order_id") or "") != order_id:
        return False
    if str(mov.get("master_item_id") or "") != master_item_id:
        return False
    if str(mov.get("reference_type") or "") != "delivery_confirmed":
        return False
    if str(mov.get("line_id") or "") != str(spec.get("line_id") or ""):
        return False
    if abs(safe_float(mov.get("transaction_qty")) - safe_float(spec.get("transaction_qty"))) > 0.0001:
        return False
    if abs(safe_float(mov.get("delta_printed")) - safe_float(spec.get("delta_printed"))) > 0.0001:
        return False
    if abs(safe_float(mov.get("delta_unprinted")) - safe_float(spec.get("delta_unprinted"))) > 0.0001:
        return False
    expected_base = abs(safe_float(spec.get("delta_printed"))) + abs(safe_float(spec.get("delta_unprinted")))
    actual_base = abs(safe_float(mov.get("delta_printed"))) + abs(safe_float(mov.get("delta_unprinted")))
    if abs(actual_base - expected_base) > 0.0001:
        return False
    return True


async def _classify_finished_delivery_claim(db, claim: Dict[str, Any]) -> tuple:
    """Prove a vanished pending from the same confirmation and every expected line.

    Returns (kind, movements). kind is complete only when source and all lines match.
    This function does not write stock or movements.
    """
    data = claim.get("claim_data") if isinstance(claim.get("claim_data"), dict) else {}
    if str(data.get("reference_type") or "delivery_confirmed") != "delivery_confirmed":
        return "other", []
    order_id = str(data.get("order_id") or "").strip()
    action_id = str(data.get("action_id") or "").strip()
    master_item_id = str(claim.get("master_item_id") or data.get("master_item_id") or "").strip()
    if not order_id or not action_id or not master_item_id:
        return "incomplete", []
    if not await is_source_committed(db, {
        "reference_type": "delivery_confirmed",
        "order_id": order_id,
        "action_id": action_id,
    }):
        return "not_committed", []
    lines = _delivery_expected_lines(claim)
    if not lines:
        return "incomplete", []
    found = []
    for spec in lines:
        mov = await _movement_by_event(db, str(spec.get("event_id") or ""))
        if not mov:
            return "incomplete", found
        if not _delivery_movement_matches_line(
            mov, spec, order_id=order_id, action_id=action_id, master_item_id=master_item_id,
        ):
            return "mismatch", []
        found.append(mov)
    if len(found) != len(lines):
        return "incomplete", found
    return "complete", found


_DELIVERY_FINISH_ERRORS = {
    "not_committed": "Konfirmasi Terkirim belum tersimpan untuk operasi ini.",
    "incomplete": "Movement Konfirmasi Terkirim belum lengkap untuk operasi ini.",
    "mismatch": "Movement tidak sesuai operasi Konfirmasi Terkirim ini.",
}


async def finalize_delivery_confirmed_stock(
    db,
    claims: List[Dict[str, Any]],
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    movements = []
    for c in claims:
        if not c:
            continue
        if c.get("status") == "ALREADY_FINALIZED":
            extra = c.get("line_movements")
            if extra:
                movements.extend(extra)
            elif c.get("movement"):
                movements.append(c.get("movement"))
            continue
        data = c.get("claim_data") if isinstance(c.get("claim_data"), dict) else {}
        pending = await _read_delivery_pending(db, str(c.get("master_item_id") or ""))
        pending_here = isinstance(pending, dict) and pending.get("event_id") == c.get("event_id")
        if not pending_here and str(data.get("reference_type") or "delivery_confirmed") == "delivery_confirmed":
            kind, rows = await _classify_finished_delivery_claim(db, c)
            if kind == "complete":
                movements.extend(rows)
                continue
            if kind in _DELIVERY_FINISH_ERRORS:
                raise RuntimeError(_DELIVERY_FINISH_ERRORS[kind])
        fence_gen = data.get("fence_generation") if "fence_generation" in data else None
        mov = await finalize_durable_stock_event(
            db,
            event_id=c["event_id"],
            master_item_id=c["master_item_id"],
            user=user,
            expected_fence_generation=None if fence_gen is None else int(fence_gen),
        )
        if isinstance(mov, dict) and mov.get("line_movements"):
            movements.extend(mov["line_movements"])
        else:
            movements.append(mov)
    return movements

