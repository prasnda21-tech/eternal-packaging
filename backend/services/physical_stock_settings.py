"""Physical Stock Settings Service (stored in app_settings with key=physical_stock).

Controls FUTURE physical-stock event recognition only.
Changing settings does NOT:
- recalculate historical stock
- rewrite old orders, receipts, or deliveries
- create historical ledger entries or backfill
- modify HPP or Accurate
- modify current_stock.py

DEFAULT: enabled = False (Zero stock mutation occurs until explicitly enabled).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional
from fastapi import HTTPException

SETTINGS_KEY = "physical_stock"

ALLOWED_STOCK_IN_TRIGGERS = frozenset({
    "goods_receipt",
    "purchase_order",
})

ALLOWED_STOCK_OUT_TRIGGERS = frozenset({
    "loading_confirmation",
    "delivery_confirmed",
    "packing_completed",
})

STOCK_IN_LABELS = {
    "goods_receipt": "Penerimaan Barang",
    "purchase_order": "Pesanan Pembelian",
}

STOCK_OUT_LABELS = {
    "loading_confirmation": "Loading Dikonfirmasi",
    "delivery_confirmed": "Pengiriman Dikonfirmasi",
    "packing_completed": "Packing Selesai",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_settings() -> Dict[str, Any]:
    return {
        "key": SETTINGS_KEY,
        "enabled": False,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "loading_confirmation",
        "updated_at": None,
        "updated_by": None,
    }


def sanitize_settings(doc: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    defaults = default_settings()
    if not isinstance(doc, dict):
        return defaults

    raw_enabled = doc.get("enabled")
    enabled = bool(raw_enabled) if raw_enabled is not None else defaults["enabled"]

    raw_in = doc.get("stock_in_trigger")
    stock_in = str(raw_in).strip() if raw_in else defaults["stock_in_trigger"]
    if stock_in not in ALLOWED_STOCK_IN_TRIGGERS:
        stock_in = defaults["stock_in_trigger"]

    raw_out = doc.get("stock_out_trigger")
    stock_out = str(raw_out).strip() if raw_out else defaults["stock_out_trigger"]
    if stock_out not in ALLOWED_STOCK_OUT_TRIGGERS:
        stock_out = defaults["stock_out_trigger"]

    return {
        "key": SETTINGS_KEY,
        "enabled": enabled,
        "stock_in_trigger": stock_in,
        "stock_out_trigger": stock_out,
        "stock_in_label": STOCK_IN_LABELS.get(stock_in, stock_in),
        "stock_out_label": STOCK_OUT_LABELS.get(stock_out, stock_out),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


async def get_physical_stock_settings(db) -> Dict[str, Any]:
    """Pure read from app_settings collection with safe fallback."""
    doc = await db.app_settings.find_one({"key": SETTINGS_KEY}, {"_id": 0})
    return sanitize_settings(doc)


async def update_physical_stock_settings(
    db,
    payload: Dict[str, Any],
    user: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Validate and persist physical stock settings.
    
    Strict validation:
    - enabled must be boolean
    - stock_in_trigger must be one of ALLOWED_STOCK_IN_TRIGGERS
    - stock_out_trigger must be one of ALLOWED_STOCK_OUT_TRIGGERS
    
    Rejects unknown triggers with HTTP 400.
    Does NOT mutate orders, receipts, deliveries, stock_movements, or HPP.
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload tidak valid")

    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="Field 'enabled' wajib diisi")

    enabled = payload["enabled"]
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="Field 'enabled' harus berupa boolean")

    stock_in = payload.get("stock_in_trigger")
    if not stock_in or not isinstance(stock_in, str):
        raise HTTPException(status_code=400, detail="Field 'stock_in_trigger' wajib diisi")
    stock_in = stock_in.strip()
    if stock_in not in ALLOWED_STOCK_IN_TRIGGERS:
        raise HTTPException(
            status_code=400,
            detail=f"Trigger stock-in tidak valid: '{stock_in}'. Pilihan valid: {sorted(ALLOWED_STOCK_IN_TRIGGERS)}",
        )

    stock_out = payload.get("stock_out_trigger")
    if not stock_out or not isinstance(stock_out, str):
        raise HTTPException(status_code=400, detail="Field 'stock_out_trigger' wajib diisi")
    stock_out = stock_out.strip()
    if stock_out not in ALLOWED_STOCK_OUT_TRIGGERS:
        raise HTTPException(
            status_code=400,
            detail=f"Trigger stock-out tidak valid: '{stock_out}'. Pilihan valid: {sorted(ALLOWED_STOCK_OUT_TRIGGERS)}",
        )

    actor_info = None
    if isinstance(user, dict):
        actor_info = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": user.get("name") or user.get("email") or "User",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }

    saved_doc = {
        "key": SETTINGS_KEY,
        "enabled": enabled,
        "stock_in_trigger": stock_in,
        "stock_out_trigger": stock_out,
        "updated_at": now_iso(),
        "updated_by": actor_info,
    }

    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": saved_doc},
        upsert=True,
    )

    return sanitize_settings(saved_doc)
