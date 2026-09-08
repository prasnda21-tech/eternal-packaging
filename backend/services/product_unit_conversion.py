"""Product Unit Conversion Setting Service.

Persists unit conversion settings attached to canonical Eternal product ID (master_item_id).
One product supports:
1 Transaction/Sales UOM -> 1 Base UOM -> 1 conversion factor (numeric, finite, > 0).

Rules:
- Setting is attached to canonical master_item_id (never parsed from product name).
- If transaction_uom == base_uom, factor must effectively be 1.
- Changing conversion factor only affects FUTURE transaction snapshots.
- Changing Base UOM is blocked once stock movement history exists.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException

COLLECTION = "product_unit_settings"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_uom(uom: Any) -> str:
    return str(uom or "").strip().upper()


def validate_conversion_factor(factor: Any) -> float:
    if factor is None:
        raise ValueError("Faktor konversi wajib diisi")
    try:
        f = float(factor)
    except (TypeError, ValueError) as e:
        raise ValueError("Faktor konversi harus berupa angka") from e

    if math.isnan(f) or math.isinf(f):
        raise ValueError("Faktor konversi tidak valid (NaN / Infinity)")
    if f <= 0:
        raise ValueError("Faktor konversi harus lebih besar dari 0")
    if f == int(f):
        return int(f)
    return round(f, 6)


async def get_product_unit_setting(db, master_item_id: str) -> Optional[Dict[str, Any]]:
    """Pure read. Returns unit setting doc or None."""
    mid = str(master_item_id or "").strip()
    if not mid:
        return None
    return await db[COLLECTION].find_one({"master_item_id": mid}, {"_id": 0})


async def save_product_unit_setting(
    db,
    master_item_id: str,
    payload: Dict[str, Any],
    user: Dict[str, Any],
) -> Dict[str, Any]:
    """Saves or updates unit setting for a product.
    
    Rejects base UOM change if product already has stock movement history.
    """
    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")

    # Find master item to ensure canonical entity exists and get base unit fallback
    master = await db.master_items.find_one({"id": mid}, {"_id": 0})
    if not master:
        # Fallback check if mid is accurate_item_id
        master = await db.master_items.find_one({"accurate_item_id": mid}, {"_id": 0})
        if master:
            mid = master["id"]
        else:
            raise HTTPException(404, f"Produk dengan ID '{mid}' tidak ditemukan")

    existing = await get_product_unit_setting(db, mid)

    base_uom = normalize_uom(payload.get("base_uom") or (existing or {}).get("base_uom") or master.get("unit") or "PCS")
    transaction_uom = normalize_uom(payload.get("transaction_uom") or (existing or {}).get("transaction_uom") or base_uom)

    if not base_uom:
        raise HTTPException(400, "Satuan Dasar (base_uom) wajib diisi")
    if not transaction_uom:
        transaction_uom = base_uom

    raw_factor = payload.get("conversion_factor")
    if transaction_uom == base_uom:
        factor = 1.0
    else:
        if raw_factor is None and existing and existing.get("transaction_uom") == transaction_uom:
            raw_factor = existing.get("conversion_factor")
        try:
            factor = validate_conversion_factor(raw_factor)
        except ValueError as e:
            raise HTTPException(400, str(e))

    # Base UOM Change Safety Check:
    # 1. If physical stock state is initialized or initializing, Base UOM CANNOT be changed away from initialized Base UOM
    state_col = getattr(db, "product_stock_state", None)
    if state_col is not None:
        state = await state_col.find_one({"$or": [{"_id": mid}, {"master_item_id": mid}]})
        if state and (state.get("initialized") or state.get("status") in ("INITIALIZING", "INITIALIZED")):
            if normalize_uom(state.get("base_uom")) != base_uom:
                raise HTTPException(
                    400,
                    "Satuan Dasar tidak dapat diubah karena saldo awal / stok fisik produk sudah diinisialisasi. "
                    "Perubahan satuan dasar akan merusak interpretasi saldo historis."
                )

    # 2. If product already has movements in ledger, Base UOM CANNOT be changed
    if existing and normalize_uom(existing.get("base_uom")) != base_uom:
        movement_count = await db.stock_movements.count_documents({
            "$or": [
                {"master_item_id": mid},
                {"accurate_item_id": master.get("accurate_item_id")},
            ]
        })
        if movement_count > 0:
            raise HTTPException(
                400,
                f"Satuan Dasar tidak dapat diubah karena produk sudah memiliki {movement_count} riwayat mutasi stok. "
                "Perubahan satuan dasar akan merusak interpretasi saldo historis."
            )

    now = now_iso()
    setting_id = (existing or {}).get("id") or str(uuid.uuid4())
    doc = {
        "id": setting_id,
        "master_item_id": mid,
        "accurate_item_id": str(master.get("accurate_item_id") or ""),
        "transaction_uom": transaction_uom,
        "base_uom": base_uom,
        "conversion_factor": factor,
        "updated_at": now,
        "updated_by": user.get("email") or user.get("id") or "system",
        "updated_by_name": user.get("name") or user.get("email") or "",
    }
    if not existing:
        doc["created_at"] = now

    await db[COLLECTION].update_one(
        {"master_item_id": mid},
        {"$set": doc},
        upsert=True,
    )

    saved = await db[COLLECTION].find_one({"master_item_id": mid}, {"_id": 0})
    return saved
