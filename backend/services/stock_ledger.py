"""Eternal Stock Movement Ledger Service.

Pure Eternal stock ledger tracking physical inventory movements:
- PENERIMAAN (Supplier -> Gudang)
- PENGIRIMAN (Gudang -> Outlet / Customer)
- PEMAKAIAN_CETAK (Gudang -> Proses Cetak / PO line)
- KOREKSI (Adjustment)

Key Guarantees:
- Bound to canonical Eternal product ID (master_item_id).
- Preserves exact line identity (line_id, line_idx, order_id).
- Stores immutable conversion snapshot (transaction_qty, factor, base_qty).
- Future product conversion changes DO NOT rewrite historical movements.
- Idempotent via event_key / action_id (duplicate events return existing movement without double counting).
- GET endpoints are pure read (zero write).
"""
from __future__ import annotations

import asyncio
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from services.product_unit_conversion import get_product_unit_setting

COLLECTION = "stock_movements"

MOVEMENT_STOK_MASUK = "STOK_MASUK"
MOVEMENT_PENERIMAAN = "PENERIMAAN"  # Canonical alias
MOVEMENT_STATUS_CETAK = "STATUS_CETAK"
MOVEMENT_PEMAKAIAN_CETAK = "PEMAKAIAN_CETAK"  # Backward-compatible alias
MOVEMENT_PENGIRIMAN = "PENGIRIMAN"
MOVEMENT_KOREKSI = "KOREKSI"
MOVEMENT_REVERSAL_PENERIMAAN = "REVERSAL_PENERIMAAN"
MOVEMENT_REVERSAL_PENGIRIMAN = "REVERSAL_PENGIRIMAN"
MOVEMENT_PEMBATALAN_PENERIMAAN = "PEMBATALAN_PENERIMAAN"
MOVEMENT_SALDO_AWAL = "SALDO_AWAL"
MOVEMENT_OPENING_BALANCE = "SALDO_AWAL"

VALID_MOVEMENT_TYPES = frozenset({
    MOVEMENT_STOK_MASUK,
    MOVEMENT_PENERIMAAN,
    MOVEMENT_STATUS_CETAK,
    MOVEMENT_PEMAKAIAN_CETAK,
    MOVEMENT_PENGIRIMAN,
    MOVEMENT_KOREKSI,
    MOVEMENT_REVERSAL_PENERIMAAN,
    MOVEMENT_REVERSAL_PENGIRIMAN,
    MOVEMENT_PEMBATALAN_PENERIMAAN,
    MOVEMENT_SALDO_AWAL,
    "OPENING_BALANCE",
})

_ledger_lock = asyncio.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or str(v).strip() == "":
            return default
        f = float(v)
        if f == int(f):
            return int(f)
        return round(f, 6)
    except (TypeError, ValueError):
        return default


async def get_latest_balance(db, master_item_id: str, accurate_item_id: Optional[str] = None) -> Optional[float]:
    """Returns the latest balance_after for this product if a recorded movement chain exists.
    Returns None if no movements or opening balance exist yet (does NOT fabricate opening history).
    """
    mid = str(master_item_id or "").strip()
    aid = str(accurate_item_id or "").strip()
    q: Dict[str, Any] = {"master_item_id": mid} if mid else {}
    if aid and not mid:
        q = {"accurate_item_id": aid}
    elif aid and mid:
        q = {"$or": [{"master_item_id": mid}, {"accurate_item_id": aid}]}

    rows = await db[COLLECTION].find(q, {"_id": 0}).sort("created_at", -1).to_list(None)
    last = next(
        (
            row for row in rows
            if str(row.get("status") or "").strip().upper() not in {"VOID", "CANCELLED"}
            and not row.get("voided_at")
        ),
        None,
    )
    if last and last.get("balance_after") is not None:
        return safe_float(last.get("balance_after", 0))

    return None


async def record_stock_movement(
    db,
    *,
    movement_type: str,
    master_item_id: str,
    accurate_item_id: Optional[str] = None,
    transaction_qty: float,
    transaction_uom: Optional[str] = None,
    source_entity: str,
    destination_entity: str,
    reference_number: str,
    reference_id: Optional[str] = None,
    description: str = "",
    order_id: Optional[str] = None,
    line_idx: Optional[int] = None,
    line_id: Optional[str] = None,
    is_print: Optional[bool] = None,
    production_process: Optional[str] = None,
    delta_unprinted: Optional[float] = None,
    delta_printed: Optional[float] = None,
    event_key: Optional[str] = None,
    user: Optional[Dict[str, Any]] = None,
    now: Optional[str] = None,
    movement_id: Optional[str] = None,
    balance_before: Optional[float] = None,
    balance_after: Optional[float] = None,
    unprinted_before: Optional[float] = None,
    unprinted_after: Optional[float] = None,
    printed_before: Optional[float] = None,
    printed_after: Optional[float] = None,
    source_event_type: Optional[str] = None,
    source_event_id: Optional[str] = None,
    reference_type: Optional[str] = None,
    change_metadata: Optional[Dict[str, Any]] = None,
    claimed_at: Optional[str] = None,
    stock_applied_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Records an immutable stock movement atomically.
    
    Idempotent: If event_key or movement_id already exists in stock_movements, returns existing doc.
    """
    m_type = str(movement_type or "").strip().upper()
    if m_type not in VALID_MOVEMENT_TYPES:
        raise ValueError(f"Tipe mutasi '{movement_type}' tidak valid")

    mid = str(master_item_id or "").strip()
    aid = str(accurate_item_id or "").strip()

    # Verify or fill master item info
    master = None
    if mid:
        master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master and aid:
        master = await db.master_items.find_one({"accurate_item_id": aid}, {"_id": 0})
        if master:
            mid = master["id"]

    if not master:
        raise ValueError(f"Master Item '{mid or aid}' tidak ditemukan")

    aid = str(master.get("accurate_item_id") or aid)

    async with _ledger_lock:
        # Idempotency check
        if event_key and str(event_key).strip():
            existing = await db[COLLECTION].find_one({"event_key": str(event_key).strip()}, {"_id": 0})
            if existing:
                return existing

        if movement_id and str(movement_id).strip():
            m_id = str(movement_id).strip()
            existing_by_id = await db[COLLECTION].find_one({"$or": [{"_id": m_id}, {"id": m_id}]}, {"_id": 0})
            if existing_by_id:
                return existing_by_id

        # Fetch unit setting snapshot
        setting = await get_product_unit_setting(db, mid)
        base_uom = (setting or {}).get("base_uom") or master.get("unit") or "PCS"
        
        tx_uom = str(transaction_uom or (setting or {}).get("transaction_uom") or base_uom).strip().upper()
        
        if tx_uom == base_uom.upper():
            factor = 1.0
        else:
            factor = safe_float((setting or {}).get("conversion_factor"), 1.0)
            if factor <= 0:
                factor = 1.0

        tx_qty = safe_float(transaction_qty)
        base_qty = safe_float(tx_qty * factor)

        # Determine CETAK vs NON CETAK
        is_p = None
        if is_print is not None:
            is_p = bool(is_print)
        elif production_process is not None:
            is_p = str(production_process).strip().lower() != "non_print"

        # 3-State Physical Stock Model:
        # TOTAL STOCK = BELUM DICETAK + SUDAH DICETAK
        # 1. STOK_MASUK / PENERIMAAN:
        #    All incoming stock enters as BELUM DICETAK (polos).
        #    delta_unprinted: +base_qty, delta_printed: 0, delta (total): +base_qty
        # 2. STATUS_CETAK / PEMAKAIAN_CETAK:
        #    Operator completion converts Belum Dicetak -> Sudah Dicetak.
        #    delta_unprinted: -base_qty, delta_printed: +base_qty, delta (total): 0 (Total stock unchanged!)
        # 3. PENGIRIMAN:
        #    If line is CETAK: shipped from Sudah Dicetak
        #    If line is NON CETAK: shipped from Belum Dicetak
        # 3b. REVERSAL_PENGIRIMAN:
        #    Bucket-correct inverse of PENGIRIMAN (restore printed or unprinted).
        # 4. KOREKSI:
        #    Adjustment directly on specified stock states.
        if m_type in (MOVEMENT_SALDO_AWAL, "OPENING_BALANCE"):
            delta = abs(base_qty)
            d_unprinted = delta_unprinted if delta_unprinted is not None else abs(base_qty)
            d_printed = delta_printed if delta_printed is not None else 0.0
            bal_before = 0.0
            bal_after = safe_float(delta)
        elif m_type in (MOVEMENT_STOK_MASUK, MOVEMENT_PENERIMAAN):
            d_unprinted = abs(base_qty)
            d_printed = 0.0
            delta = abs(base_qty)
            bal_before = await get_latest_balance(db, mid, aid)
            bal_after = safe_float(bal_before + delta) if bal_before is not None else None
        elif m_type in (MOVEMENT_REVERSAL_PENERIMAAN, MOVEMENT_PEMBATALAN_PENERIMAAN):
            d_unprinted = -abs(base_qty)
            d_printed = 0.0
            delta = -abs(base_qty)
            bal_before = await get_latest_balance(db, mid, aid)
            bal_after = safe_float(bal_before + delta) if bal_before is not None else None
        elif m_type in (MOVEMENT_STATUS_CETAK, MOVEMENT_PEMAKAIAN_CETAK):
            d_unprinted = -abs(base_qty)
            d_printed = abs(base_qty)
            delta = 0.0  # Invariant: Operator print conversion DOES NOT change total stock!
            bal_before = await get_latest_balance(db, mid, aid)
            bal_after = safe_float(bal_before + delta) if bal_before is not None else None
        elif m_type == MOVEMENT_PENGIRIMAN:
            if is_p is False:
                d_unprinted = -abs(base_qty)
                d_printed = 0.0
            else:
                d_unprinted = 0.0
                d_printed = -abs(base_qty)
            delta = -abs(base_qty)
            bal_before = await get_latest_balance(db, mid, aid)
            bal_after = safe_float(bal_before + delta) if bal_before is not None else None
        elif m_type == MOVEMENT_REVERSAL_PENGIRIMAN:
            # Prefer explicit deltas from durable claim/finalize when provided.
            if delta_unprinted is not None or delta_printed is not None:
                d_unprinted = safe_float(delta_unprinted)
                d_printed = safe_float(delta_printed)
                delta = safe_float(d_unprinted + d_printed)
            elif is_p is False:
                d_unprinted = abs(base_qty)
                d_printed = 0.0
                delta = abs(base_qty)
            else:
                d_unprinted = 0.0
                d_printed = abs(base_qty)
                delta = abs(base_qty)
            bal_before = await get_latest_balance(db, mid, aid)
            bal_after = safe_float(bal_before + delta) if bal_before is not None else None
        else:  # KOREKSI
            delta = base_qty
            d_unprinted = delta_unprinted if delta_unprinted is not None else base_qty
            d_printed = delta_printed if delta_printed is not None else 0.0
        if balance_before is not None:
            bal_before = safe_float(balance_before)
            bal_after = safe_float(balance_after) if balance_after is not None else safe_float(bal_before + delta)

        stamp = now or now_iso()
        mov_id = str(movement_id or uuid.uuid4()).strip()

        doc = {
            "_id": mov_id,
            "id": mov_id,
            "event_key": str(event_key).strip() if event_key else None,
            "master_item_id": mid,
            "accurate_item_id": aid,
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or "",
            "movement_type": m_type,
            "date": stamp,
            "created_at": stamp,
            "reference_id": str(reference_id or "").strip() or None,
            "reference_number": str(reference_number or "").strip(),
            "reference_type": str(reference_type or "").strip() or None,
            "source_event_type": str(source_event_type or "").strip() or None,
            "source_event_id": str(source_event_id or "").strip() or None,
            "description": str(description or "").strip(),
            "source": str(source_entity or "").strip(),
            "destination": str(destination_entity or "").strip(),
            "transaction_qty": tx_qty,
            "transaction_uom": tx_uom,
            "conversion_factor": factor,
            "base_qty": base_qty,
            "base_uom": base_uom,
            "delta": delta,
            "delta_unprinted": d_unprinted,
            "delta_printed": d_printed,
            "is_print": is_p,
            "production_process": str(production_process).strip().lower() if production_process else None,
            "balance_before": bal_before,
            "balance_after": bal_after,
            "claimed_at": str(claimed_at).strip() if claimed_at else None,
            "stock_applied_at": str(stock_applied_at).strip() if stock_applied_at else None,
            "unprinted_before": safe_float(unprinted_before) if unprinted_before is not None else None,
            "unprinted_after": safe_float(unprinted_after) if unprinted_after is not None else None,
            "printed_before": safe_float(printed_before) if printed_before is not None else None,
            "printed_after": safe_float(printed_after) if printed_after is not None else None,
            "order_id": str(order_id or "").strip() or None,
            "line_idx": line_idx,
            "line_id": str(line_id or "").strip() or None,
            "actor": {
                "email": (user or {}).get("email") or (user or {}).get("id") or "system",
                "name": (user or {}).get("name") or (user or {}).get("email") or "Sistem",
                "role": (user or {}).get("role") or "",
            },
        }
        if change_metadata:
            doc.update(change_metadata)

        try:
            await db[COLLECTION].insert_one(doc)
        except DuplicateKeyError:
            existing = await db[COLLECTION].find_one({"$or": [{"_id": mov_id}, {"id": mov_id}]}, {"_id": 0})
            if existing:
                return existing
            raise
        doc.pop("_id", None)
        return doc


async def list_product_mutations(
    db,
    master_item_id: str,
    *,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    movement_type: Optional[str] = None,
    q: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """Pure read. Returns paginated stock movements for a specific product."""
    mid = str(master_item_id or "").strip()
    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master:
        master = await db.master_items.find_one({"accurate_item_id": mid}, {"_id": 0})
        if master:
            mid = master["id"]

    if not master:
        raise HTTPException(404, f"Produk '{mid}' tidak ditemukan")

    aid = master.get("accurate_item_id")
    filter_q: Dict[str, Any] = {
        "$or": [
            {"master_item_id": mid},
            {"accurate_item_id": aid},
        ]
    }

    # Date filter
    date_cond: Dict[str, Any] = {}
    if start_date and str(start_date).strip():
        date_cond["$gte"] = str(start_date).strip()[:10]
    if end_date and str(end_date).strip():
        # Append end of day if only YYYY-MM-DD
        ed = str(end_date).strip()[:10]
        date_cond["$lte"] = f"{ed}T23:59:59.999999Z" if "T" not in ed else ed

    if date_cond:
        filter_q["date"] = date_cond

    # Movement type filter
    mt = str(movement_type or "").strip().upper()
    if mt and mt not in ("ALL", "SEMUA"):
        if mt in (MOVEMENT_STOK_MASUK, MOVEMENT_PENERIMAAN):
            filter_q["movement_type"] = {"$in": [MOVEMENT_STOK_MASUK, MOVEMENT_PENERIMAAN]}
        elif mt in (MOVEMENT_STATUS_CETAK, MOVEMENT_PEMAKAIAN_CETAK):
            filter_q["movement_type"] = {"$in": [MOVEMENT_STATUS_CETAK, MOVEMENT_PEMAKAIAN_CETAK]}
        elif mt in VALID_MOVEMENT_TYPES:
            filter_q["movement_type"] = mt

    # Search filter: reference_number, description, source, destination
    needle = str(q or "").strip()
    if needle:
        rgx = {"$regex": re.escape(needle), "$options": "i"}
        filter_q["$and"] = filter_q.get("$and", [])
        filter_q["$and"].append({
            "$or": [
                {"reference_number": rgx},
                {"description": rgx},
                {"source": rgx},
                {"destination": rgx},
            ]
        })

    total = await db[COLLECTION].count_documents(filter_q)
    p = max(1, int(page or 1))
    ps = max(1, min(int(page_size or 50), 200))
    skip = (p - 1) * ps

    rows = await db[COLLECTION].find(filter_q, {"_id": 0}).sort("date", -1).skip(skip).limit(ps).to_list(ps)

    return {
        "items": rows,
        "total": total,
        "page": p,
        "page_size": ps,
        "page_count": max(1, (total + ps - 1) // ps) if total else 1,
    }


async def get_mutation_detail(db, mutation_id: str) -> Optional[Dict[str, Any]]:
    """Pure read. Returns detail of a single stock movement."""
    mid = str(mutation_id or "").strip()
    if not mid:
        return None
    return await db[COLLECTION].find_one({"id": mid}, {"_id": 0})
