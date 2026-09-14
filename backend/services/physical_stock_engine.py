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
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument

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
    user: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
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
            if await is_source_committed(db, pending):
                mov = await finalize_durable_stock_event(db, event_id=ev_id, master_item_id=mid, user=user)
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
        "accurate_item_id": aid,
        "claimed_at": stamp,
        "started_at": stamp,
    }

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

    if stage == "CLAIMED":
        d_tot = safe_float(pending.get("delta_total", 0.0))
        d_unp = safe_float(pending.get("delta_unprinted", 0.0))
        d_pri = safe_float(pending.get("delta_printed", 0.0))

        await db[STATE_COLLECTION].update_one(
            {"$or": [{"_id": mid}, {"master_item_id": mid}], "pending_event.event_id": ev_id, "pending_event.stage": "CLAIMED"},
            {
                "$inc": {
                    "total_qty": d_tot,
                    "unprinted_qty": d_unp,
                    "printed_qty": d_pri,
                    "version": 1,
                },
                "$set": {
                    "pending_event.stage": "STATE_APPLIED",
                    "updated_at": stamp,
                }
            }
        )

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
        unprinted_before=pending.get("unprinted_before", 0.0),
        unprinted_after=pending.get("unprinted_after", 0.0),
        printed_before=pending.get("printed_before", 0.0),
        printed_after=pending.get("printed_after", 0.0),
        source_event_type=pending.get("reference_type", ""),
        source_event_id=ev_id,
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

        act_id = str(inc.get("action_id") or uuid.uuid4())
        event_id = f"stock_out:packing:{order_id}:{act_id}:{idx}"
        proc = str(target_line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"

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

        event_id = f"stock_out:loading:{order_id}:{rec_id}:{idx}"
        proc = str(target_line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"

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

        line_id = str(line.get("line_id") or line.get("line_idx", 0))
        event_id = f"stock_out:sales_delivery:{del_id}:{line_id}"
        proc = str(line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"

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

        event_id = f"stock_out:delivery:{order_id}:{conf_id}:{idx}"
        proc = str(line.get("production_process") or "").strip().lower()
        is_p = proc != "non_print"

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
        bucket = "printed" if is_p else "unprinted"
        base_qty, _, _, _ = await resolve_transaction_conversion(
            db, mid, qty, line.get("unit")
        )
        await check_stock_availability(
            db, mid, base_qty, bucket=bucket, context_label="pengiriman Surat Jalan"
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


async def claim_delivery_confirmed_stock(
    db,
    order: Dict[str, Any],
    lines: List[Dict[str, Any]],
    action_id: str,
    user: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    claims = []
    lis = order.get("line_items") or []
    try:
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
            event_id = f"stock_out:delivery:{order['id']}:{action_id}:{idx}"
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
                reference_type="delivery_confirmed",
                reference_id=order.get("id"),
                description=f"Pengurangan Stok Fisik Konfirmasi Diterima ({'Cetak' if is_p else 'Polos'})",
                order_id=order.get("id"),
                line_idx=idx,
                line_id=line.get("line_id"),
                action_id=action_id,
                is_print=is_p,
                production_process=proc,
                user=user,
            )
            claims.append(claim)
        return claims
    except Exception:
        await release_delivery_confirmed_stock(db, claims)
        raise


async def release_delivery_confirmed_stock(
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

