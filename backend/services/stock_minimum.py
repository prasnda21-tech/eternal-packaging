"""Per item × branch × warehouse minimum stock thresholds.

Write path only mutates item_stock_minimums documents.
Never posts ledger, balances, orders, HPP, or Accurate.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

COLLECTION = "item_stock_minimums"
PERM_MANAGE = "stock_minimum.manage"
PERM_VIEW = "current_stock.view"

UNIQUE_KEYS = ("master_item_id", "branch_id", "warehouse_id")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite_non_negative(value: Any, *, field: str = "minimum_qty") -> Optional[float]:
    """None/empty → unset. 0 is a valid configured minimum."""
    if value is None:
        return None
    if isinstance(value, str) and not str(value).strip():
        return None
    if isinstance(value, bool):
        raise HTTPException(400, f"{field} harus angka")
    try:
        n = float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, f"{field} harus angka") from exc
    if math.isnan(n) or math.isinf(n):
        raise HTTPException(400, f"{field} tidak valid")
    if n < 0:
        raise HTTPException(400, f"{field} tidak boleh negatif")
    if n == int(n):
        return int(n)
    return n


def public_minimum(doc: Optional[dict]) -> Optional[dict]:
    if not isinstance(doc, dict):
        return None
    raw = doc.get("minimum_qty")
    configured = raw is not None
    return {
        "id": doc.get("id"),
        "master_item_id": doc.get("master_item_id"),
        "branch_id": doc.get("branch_id"),
        "warehouse_id": doc.get("warehouse_id"),
        "minimum_qty": raw if configured else None,
        "configured": configured,
        "unit": doc.get("unit") or "PCS",
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
    }


def _actor_info(user: Optional[dict]) -> Optional[dict]:
    if not isinstance(user, dict):
        return None
    return {
        "id": str(user.get("id") or user.get("_id") or ""),
        "name": user.get("name") or user.get("email") or "User",
        "email": user.get("email") or "",
        "role": user.get("role") or "",
    }


async def ensure_indexes(db) -> None:
    await db[COLLECTION].create_index(
        [("master_item_id", 1), ("branch_id", 1), ("warehouse_id", 1)],
        unique=True,
        name="item_stock_minimum_unique",
    )


async def get_minimum(
    db,
    *,
    master_item_id: str,
    branch_id: str,
    warehouse_id: str,
) -> Optional[dict]:
    mid = str(master_item_id or "").strip()
    bid = str(branch_id or "").strip()
    wid = str(warehouse_id or "").strip()
    if not (mid and bid and wid):
        return None
    doc = await db[COLLECTION].find_one(
        {"master_item_id": mid, "branch_id": bid, "warehouse_id": wid},
        {"_id": 0},
    )
    return public_minimum(doc)


async def list_minimums(
    db,
    *,
    branch_id: Optional[str] = None,
    warehouse_id: Optional[str] = None,
    master_item_ids: Optional[List[str]] = None,
) -> List[dict]:
    query: Dict[str, Any] = {}
    bid = str(branch_id or "").strip()
    wid = str(warehouse_id or "").strip()
    if bid:
        query["branch_id"] = bid
    if wid:
        query["warehouse_id"] = wid
    if master_item_ids:
        ids = [str(x).strip() for x in master_item_ids if str(x).strip()]
        if ids:
            query["master_item_id"] = {"$in": ids}
    rows = await db[COLLECTION].find(query, {"_id": 0}).to_list(20000)
    return [public_minimum(row) for row in rows if public_minimum(row)]


async def upsert_minimum(
    db,
    *,
    master_item_id: str,
    branch_id: str,
    warehouse_id: str,
    minimum_qty: Any,
    unit: str = "PCS",
    user: Optional[dict] = None,
    clear: bool = False,
) -> dict:
    mid = str(master_item_id or "").strip()
    bid = str(branch_id or "").strip()
    wid = str(warehouse_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")
    if not bid:
        raise HTTPException(400, "branch_id wajib diisi")
    if not wid:
        raise HTTPException(400, "warehouse_id wajib diisi")

    warehouse = await db.warehouses.find_one({"id": wid}, {"_id": 0, "id": 1, "branch_id": 1, "active": 1})
    if not warehouse:
        raise HTTPException(404, "Gudang tidak ditemukan")
    if str(warehouse.get("branch_id") or "") != bid:
        raise HTTPException(400, "Gudang tidak berada di cabang yang dipilih")

    master = await db.master_items.find_one({"id": mid}, {"_id": 0, "id": 1, "unit": 1, "item_type": 1})
    if not master:
        raise HTTPException(404, "Master barang tidak ditemukan")

    if clear or (isinstance(minimum_qty, str) and not str(minimum_qty).strip()) or minimum_qty is None:
        await db[COLLECTION].delete_one(
            {"master_item_id": mid, "branch_id": bid, "warehouse_id": wid},
        )
        return {
            "minimum": {
                "master_item_id": mid,
                "branch_id": bid,
                "warehouse_id": wid,
                "minimum_qty": None,
                "configured": False,
                "unit": str(unit or master.get("unit") or "PCS"),
                "updated_at": now_iso(),
                "updated_by": _actor_info(user),
            },
            "cleared": True,
        }

    qty = _finite_non_negative(minimum_qty)
    assert qty is not None
    unit_norm = str(unit or master.get("unit") or "PCS").strip().upper() or "PCS"
    existing = await db[COLLECTION].find_one(
        {"master_item_id": mid, "branch_id": bid, "warehouse_id": wid},
        {"_id": 0},
    )
    doc = {
        "id": (existing or {}).get("id") or str(uuid.uuid4()),
        "master_item_id": mid,
        "branch_id": bid,
        "warehouse_id": wid,
        "minimum_qty": qty,
        "unit": unit_norm,
        "updated_at": now_iso(),
        "updated_by": _actor_info(user),
    }
    await db[COLLECTION].update_one(
        {"master_item_id": mid, "branch_id": bid, "warehouse_id": wid},
        {"$set": doc},
        upsert=True,
    )
    return {"minimum": public_minimum(doc), "cleared": False}


def evaluate_minimum_status(
    *,
    stock_dapat_dijual: Any,
    sellable_status: Optional[str],
    minimum_qty: Any,
    minimum_configured: bool,
) -> Dict[str, Any]:
    """Classify widget/SSI minimum row status."""
    if not minimum_configured or minimum_qty is None:
        return {
            "status": "MINIMUM_UNSET",
            "label": "Minimum belum diatur",
            "is_violation": False,
            "evaluable": False,
        }
    st = str(sellable_status or "").strip().upper()
    if stock_dapat_dijual is None or st in ("INCOMPLETE", "UNVERIFIED", "BELUM_TERSEDIA"):
        return {
            "status": "SOURCE_INCOMPLETE",
            "label": "Sumber belum lengkap",
            "is_violation": False,
            "evaluable": False,
        }
    try:
        atp = float(stock_dapat_dijual)
        minimum = float(minimum_qty)
    except (TypeError, ValueError):
        return {
            "status": "SOURCE_INCOMPLETE",
            "label": "Sumber belum lengkap",
            "is_violation": False,
            "evaluable": False,
        }
    if math.isnan(atp) or math.isinf(atp) or math.isnan(minimum) or math.isinf(minimum):
        return {
            "status": "SOURCE_INCOMPLETE",
            "label": "Sumber belum lengkap",
            "is_violation": False,
            "evaluable": False,
        }
    if atp <= minimum:
        return {
            "status": "BELOW_OR_AT_MINIMUM",
            "label": "Di/bawah minimum",
            "is_violation": True,
            "evaluable": True,
        }
    return {
        "status": "OK",
        "label": "Aman",
        "is_violation": False,
        "evaluable": True,
    }
