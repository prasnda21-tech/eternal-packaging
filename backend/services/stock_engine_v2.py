"""Stock Engine V2 — single source of truth on product_stock_state + stock_movements.

Physical stock (STOCK FISIK) =
  Saldo Awal
  + Penerimaan Barang pembelian / Goods Receipt resmi (VALID / durable movements)
  ± Stock Opname (KOREKSI)
  − Pengiriman yang benar-benar keluar (PENGIRIMAN / sales delivery)

FINAL FORMULA:
  Outstanding SO = Qty SO aktif − Qty sudah dikirim
  Belum Dicetak  = MAX(Outstanding SO − Sudah Dicetak, 0)
  Sudah Dicetak  = dari production progress (printed bucket)
  Stock Saat Ini / Available = MAX(Fisik − Outstanding SO, 0)

Rules:
  - Cetak TIDAK memotong Available lagi (hanya memindah Belum Dicetak → Sudah Dicetak)
  - Delivery menurunkan Fisik dan Outstanding dengan qty yang sama
  - Hanya GR resmi yang menambah stock
  - PO / Faktur / Pembayaran TIDAK menambah stock
  - manual_purchase_qty dilarang / dihapus dari perhitungan

Tidak membuat engine stock ketiga.
"""
from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException
from pymongo import ReturnDocument

from services.product_stock_state import COLLECTION as STATE_COLLECTION, get_product_stock_state
from services.stock_ledger import (
    MOVEMENT_KOREKSI,
    record_stock_movement,
    safe_float,
)

STOCK_INSUFFICIENT_MSG = "Stock tersedia tidak mencukupi"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def qty_num(v: Any) -> float:
    try:
        if v is None or (isinstance(v, str) and not str(v).strip()):
            return 0.0
        n = float(v)
        if math.isnan(n) or math.isinf(n):
            return 0.0
        if n == int(n):
            return float(int(n))
        return n
    except (TypeError, ValueError):
        return 0.0


def compute_available(physical_qty: Any, outstanding_so_qty: Any, printed_qty: Any = 0) -> float:
    """STOCK SAAT INI / Available = MAX(PHYSICAL - OUTSTANDING_SO, 0).

    Breakdown (display only; must not double-count):
      SUDAH_DICETAK = printed portion of outstanding / production
      BELUM_DICETAK = MAX(OUTSTANDING_SO - SUDAH_DICETAK, 0)
      STOCK_POLOS   = MAX(PHYSICAL - SUDAH_DICETAK, 0)
      AVAILABLE     = MAX(STOCK_POLOS - BELUM_DICETAK, 0)
                    ≡ MAX(PHYSICAL - OUTSTANDING_SO, 0)

    `printed_qty` is accepted for API compatibility but does not change Available.
    Printing only moves BELUM → SUDAH; delivery drops Fisik and Outstanding together.
    """
    avail = max(qty_num(physical_qty) - qty_num(outstanding_so_qty), 0.0)
    if avail == int(avail):
        return float(int(avail))
    return avail


def compute_stock_polos(physical_qty: Any, printed_qty: Any) -> float:
    """Stock Polos Fisik = MAX(PHYSICAL - SUDAH_DICETAK, 0)."""
    return max(qty_num(physical_qty) - qty_num(printed_qty), 0.0)


def compute_belum_dicetak(outstanding_so_qty: Any, printed_qty: Any) -> float:
    """Belum Dicetak = MAX(OUTSTANDING_SO - SUDAH_DICETAK, 0)."""
    return max(qty_num(outstanding_so_qty) - qty_num(printed_qty), 0.0)


def compute_unprinted(physical_qty: Any, printed_qty: Any) -> float:
    """Legacy alias: physical unprinted bucket ≈ stock polos when buckets consistent."""
    return compute_stock_polos(physical_qty, printed_qty)


def compute_pollos_reserved(outstanding_so_qty: Any, printed_qty: Any) -> float:
    """Legacy alias for Belum Dicetak (outstanding not yet printed)."""
    return compute_belum_dicetak(outstanding_so_qty, printed_qty)


def identity_keys(doc: dict) -> List[str]:
    keys: List[str] = []
    aid = str(doc.get("accurate_item_id") or doc.get("accurate_id") or "").strip()
    mid = str(doc.get("master_item_id") or doc.get("id") or "").strip()
    if aid:
        keys.append(f"a:{aid}")
    if mid:
        keys.append(f"m:{mid}")
    return keys


def _add(bucket: Dict[str, float], aliases: Dict[str, str], doc: dict, amount: Any) -> None:
    n = qty_num(amount)
    keys = identity_keys(doc)
    if not keys:
        return
    canon = None
    for k in keys:
        if k in aliases:
            canon = aliases[k]
            break
    if canon is None:
        canon = keys[0]
        bucket.setdefault(canon, 0.0)
    for k in keys:
        aliases[k] = canon
    bucket[canon] = qty_num(bucket.get(canon, 0)) + n


def _get(bucket: Dict[str, float], aliases: Dict[str, str], doc: dict) -> float:
    for k in identity_keys(doc):
        canon = aliases.get(k)
        if canon is not None:
            return qty_num(bucket.get(canon, 0))
    return 0.0


def _line_item_identity(li: dict) -> dict:
    return {
        "accurate_item_id": li.get("accurate_item_id") or li.get("accurate_id"),
        "master_item_id": li.get("master_item_id") or li.get("item_id"),
        "id": li.get("master_item_id") or li.get("item_id"),
    }


def aggregate_outstanding_so(
    orders: Iterable[dict],
    deliveries: Iterable[dict],
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Outstanding = ordered − shipped, per item identity. Cancelled orders excluded."""
    ordered: Dict[str, float] = {}
    aliases: Dict[str, str] = {}
    order_by_id: Dict[str, dict] = {}

    for order in orders:
        if order.get("cancelled") or order.get("is_cancelled"):
            continue
        status = str(order.get("status") or order.get("status_label") or "").strip().lower()
        if status in ("cancelled", "dibatalkan", "void"):
            continue
        oid = str(order.get("id") or "").strip()
        if oid:
            order_by_id[oid] = order
        for li in order.get("line_items") or []:
            ident = _line_item_identity(li)
            if not identity_keys(ident):
                continue
            _add(ordered, aliases, ident, li.get("quantity"))

    shipped: Dict[str, float] = {}
    ship_alias: Dict[str, str] = dict(aliases)
    for delivery in deliveries:
        if delivery.get("is_cancelled") or delivery.get("cancelled"):
            continue
        po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()
        order = order_by_id.get(po_id)
        lines = list(delivery.get("line_items") or delivery.get("lines") or [])
        for ln in lines:
            if not isinstance(ln, dict):
                continue
            ident = _line_item_identity(ln)
            # Prefer explicit identity; else map via line_idx on parent order
            if not identity_keys(ident) and order is not None:
                try:
                    idx = int(ln.get("line_idx"))
                except (TypeError, ValueError):
                    idx = None
                order_lines = order.get("line_items") or []
                if idx is not None and 0 <= idx < len(order_lines):
                    ident = _line_item_identity(order_lines[idx])
            if not identity_keys(ident):
                continue
            qty = ln.get("qty")
            if qty is None:
                qty = ln.get("quantity")
            if qty is None:
                qty = ln.get("shipped_qty")
            _add(shipped, ship_alias, ident, qty)

    # Merge alias maps so shipped uses same canons as ordered when possible
    for k, v in ship_alias.items():
        aliases.setdefault(k, v)

    outstanding: Dict[str, float] = {}
    all_canons = set(ordered.keys()) | set(shipped.keys())
    # Remap shipped canons via aliases
    shipped_by_ordered_canon: Dict[str, float] = {}
    for k, amt in shipped.items():
        canon = aliases.get(k, k)
        # If ship key itself is a canon under aliases values, keep
        shipped_by_ordered_canon[canon] = qty_num(shipped_by_ordered_canon.get(canon, 0)) + qty_num(amt)

    for canon in set(ordered.keys()) | set(shipped_by_ordered_canon.keys()):
        rem = max(0.0, qty_num(ordered.get(canon, 0)) - qty_num(shipped_by_ordered_canon.get(canon, 0)))
        outstanding[canon] = rem
    return outstanding, aliases


def physical_from_state(state: Optional[dict]) -> Dict[str, float]:
    if not state or not (state.get("initialized") or state.get("status") == "INITIALIZED"):
        return {
            "physical_qty": 0.0,
            "unprinted_qty": 0.0,
            "printed_qty": 0.0,
        }
    unp = qty_num(state.get("unprinted_qty"))
    pri = qty_num(state.get("printed_qty"))
    tot = qty_num(state.get("total_qty"))
    if abs(tot - (unp + pri)) > 1e-6:
        tot = unp + pri
    return {"physical_qty": tot, "unprinted_qty": unp, "printed_qty": pri}


async def load_state_map(db) -> Dict[str, dict]:
    out: Dict[str, dict] = {}
    async for doc in db[STATE_COLLECTION].find({}, {"_id": 0}):
        mid = str(doc.get("master_item_id") or doc.get("id") or "").strip()
        if mid:
            out[mid] = doc
        aid = str(doc.get("accurate_item_id") or "").strip()
        if aid:
            out[f"a:{aid}"] = doc
    return out


def build_stock_rows_v2(
    master_items: Iterable[dict],
    *,
    state_by_master: Dict[str, dict],
    outstanding_bucket: Dict[str, float],
    outstanding_alias: Dict[str, str],
) -> List[dict]:
    """Stock Saat Ini rows from physical ledger + outstanding SO (no manual purchase)."""
    rows: List[dict] = []
    for it in master_items:
        mid = str(it.get("id") or it.get("master_item_id") or "").strip()
        aid = str(it.get("accurate_item_id") or "").strip()
        if not mid and not aid:
            continue
        state = state_by_master.get(mid) or (state_by_master.get(f"a:{aid}") if aid else None)
        phys = physical_from_state(state)
        outstanding = _get(outstanding_bucket, outstanding_alias, {
            "master_item_id": mid,
            "accurate_item_id": aid,
            "id": mid,
        })
        printed = phys["printed_qty"]
        belum = compute_belum_dicetak(outstanding, printed)
        polos = compute_stock_polos(phys["physical_qty"], printed)
        available = compute_available(phys["physical_qty"], outstanding)
        updated_at = (state or {}).get("updated_at") or (state or {}).get("initialized_at")
        last_opname_at = (state or {}).get("last_opname_at")
        rows.append({
            "master_item_id": mid or None,
            "accurate_item_id": aid or None,
            "item_code": it.get("item_code") or "",
            "item_name": it.get("item_name") or "",
            "unit": it.get("unit") or "",
            # V2 columns
            "physical_qty": phys["physical_qty"],
            # Belum Dicetak (SO breakdown) — NOT the raw physical unprinted bucket
            "unprinted_qty": belum,
            "belum_dicetak_qty": belum,
            "printed_qty": printed,
            "sudah_dicetak_qty": printed,
            "physical_unprinted_qty": phys["unprinted_qty"],
            "stock_polos_qty": polos,
            "outstanding_so_qty": outstanding,
            "allocated_qty": outstanding,
            "stock_current": available,
            "available_qty": available,
            # Legacy keys kept for consumers that still read stock_current only
            "qty_po": outstanding,  # semantic shift: now outstanding SO, not full PO qty
            "manual_current_qty": phys["physical_qty"],
            "purchase_qty": 0,
            "purchase_source": "GOODS_RECEIPT",
            "operator_print_qty": 0,  # print moves Belum→Sudah; does not re-cut Available
            "manual_purchase_qty": 0,
            "updated_at": updated_at,
            "last_opname_at": last_opname_at,
            "updated_by": (state or {}).get("initialized_by", {}).get("email") if isinstance((state or {}).get("initialized_by"), dict) else None,
            "updated_by_name": (state or {}).get("initialized_by", {}).get("name") if isinstance((state or {}).get("initialized_by"), dict) else None,
            "formula": "available = max(physical - outstanding_so, 0); belum = max(outstanding_so - printed, 0)",
            "stock_engine": "v2",
        })
    return rows


async def build_current_stock_rows(db) -> List[dict]:
    masters = await db.master_items.find({}, {"_id": 0}).to_list(10000)
    orders = await db.orders.find(
        {"cancelled": {"$ne": True}, "is_cancelled": {"$ne": True}},
        {
            "_id": 0,
            "id": 1,
            "cancelled": 1,
            "is_cancelled": 1,
            "status": 1,
            "status_label": 1,
            "line_items.quantity": 1,
            "line_items.accurate_item_id": 1,
            "line_items.master_item_id": 1,
            "line_items.item_id": 1,
        },
    ).to_list(20000)
    deliveries = await db.sales_deliveries.find(
        {"is_cancelled": {"$ne": True}, "cancelled": {"$ne": True}},
        {
            "_id": 0,
            "po_id": 1,
            "order_id": 1,
            "is_cancelled": 1,
            "cancelled": 1,
            "line_items": 1,
            "lines": 1,
        },
    ).to_list(20000)
    outstanding, alias = aggregate_outstanding_so(orders, deliveries)
    state_map = await load_state_map(db)
    return build_stock_rows_v2(
        masters,
        state_by_master=state_map,
        outstanding_bucket=outstanding,
        outstanding_alias=alias,
    )


def matches_search(item: dict, q: str) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return True
    return any(
        needle in str(item.get(k) or "").lower()
        for k in ("item_code", "item_name", "accurate_item_id", "master_item_id")
    )


def stock_sign_filter(stock: float, stock_filter: str) -> bool:
    f = (stock_filter or "all").strip().lower()
    if f in ("", "all", "semua"):
        return True
    if f in ("positive", "positif"):
        return stock > 0
    if f in ("zero", "nol"):
        return stock == 0
    if f in ("negative", "negatif"):
        return stock < 0
    return True


SORT_KEYS = {
    "name": "item_name",
    "item_name": "item_name",
    "physical_qty": "physical_qty",
    "outstanding_so_qty": "outstanding_so_qty",
    "allocated_qty": "outstanding_so_qty",
    "stock_current": "stock_current",
    "available_qty": "stock_current",
    "qty_po": "outstanding_so_qty",
}


def filter_sort_page_v2(
    rows: List[dict],
    *,
    q: str = "",
    stock_filter: str = "all",
    sort_by: str = "name",
    sort_dir: str = "asc",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    filtered = [
        r for r in rows
        if matches_search(r, q) and stock_sign_filter(qty_num(r.get("stock_current")), stock_filter)
    ]
    key = SORT_KEYS.get((sort_by or "name").strip().lower(), "item_name")
    reverse = (sort_dir or "asc").strip().lower() == "desc"

    def sort_val(r):
        v = r.get(key)
        if key == "item_name":
            return str(v or "").lower()
        return qty_num(v)

    filtered.sort(key=sort_val, reverse=reverse)
    total = len(filtered)
    page = max(1, int(page or 1))
    page_size = max(1, min(int(page_size or 50), 100))
    start = (page - 1) * page_size
    page_rows = filtered[start:start + page_size]
    return {
        "items": page_rows,
        "total": total,
        "page": page,
        "page_size": page_size,
        "page_count": max(1, (total + page_size - 1) // page_size) if total else 1,
        "purchase_source": "GOODS_RECEIPT",
        "formula": "Stock Saat Ini = MAX(Fisik − Outstanding SO, 0)",
        "stock_engine": "v2",
        "manual_purchase_removed": True,
    }


async def available_for_item(db, *, master_item_id: Optional[str] = None, accurate_item_id: Optional[str] = None) -> Dict[str, float]:
    rows = await build_current_stock_rows(db)
    mid = str(master_item_id or "").strip()
    aid = str(accurate_item_id or "").strip()
    for r in rows:
        if mid and str(r.get("master_item_id") or "") == mid:
            return r
        if aid and str(r.get("accurate_item_id") or "") == aid:
            return r
    return {
        "physical_qty": 0.0,
        "outstanding_so_qty": 0.0,
        "stock_current": 0.0,
        "available_qty": 0.0,
    }


async def validate_sales_order_availability(
    db,
    line_items: Iterable[dict],
    *,
    exclude_order_id: Optional[str] = None,
) -> None:
    """Block SO create/revise when requested qty > available.

    For revise, exclude_order_id removes that order's current outstanding from the
    denominator so the revision is compared against free stock + its own prior reservation.
    """
    # Build baseline outstanding excluding this order
    masters = await db.master_items.find({}, {"_id": 0, "id": 1, "accurate_item_id": 1}).to_list(10000)
    orders = await db.orders.find(
        {"cancelled": {"$ne": True}, "is_cancelled": {"$ne": True}},
        {
            "_id": 0,
            "id": 1,
            "cancelled": 1,
            "is_cancelled": 1,
            "status": 1,
            "line_items.quantity": 1,
            "line_items.accurate_item_id": 1,
            "line_items.master_item_id": 1,
            "line_items.item_id": 1,
        },
    ).to_list(20000)
    if exclude_order_id:
        oid = str(exclude_order_id)
        orders = [o for o in orders if str(o.get("id")) != oid]
    deliveries = await db.sales_deliveries.find(
        {"is_cancelled": {"$ne": True}, "cancelled": {"$ne": True}},
        {"_id": 0, "po_id": 1, "order_id": 1, "line_items": 1, "lines": 1, "is_cancelled": 1, "cancelled": 1},
    ).to_list(20000)
    if exclude_order_id:
        oid = str(exclude_order_id)
        deliveries = [
            d for d in deliveries
            if str(d.get("po_id") or "") != oid and str(d.get("order_id") or "") != oid
        ]
    outstanding, alias = aggregate_outstanding_so(orders, deliveries)
    state_map = await load_state_map(db)

    # Aggregate requested qty by item
    req_bucket: Dict[str, float] = {}
    req_alias: Dict[str, str] = dict(alias)
    for li in line_items or []:
        ident = _line_item_identity(li)
        if not identity_keys(ident):
            continue
        _add(req_bucket, req_alias, ident, li.get("quantity"))

    for li in line_items or []:
        ident = _line_item_identity(li)
        keys = identity_keys(ident)
        if not keys:
            continue
        mid = str(ident.get("master_item_id") or "").strip()
        aid = str(ident.get("accurate_item_id") or "").strip()
        state = state_map.get(mid) or (state_map.get(f"a:{aid}") if aid else None)
        phys_state = physical_from_state(state)
        phys = phys_state["physical_qty"]
        printed = phys_state["printed_qty"]
        already_out = _get(outstanding, alias, ident)
        requested = _get(req_bucket, req_alias, ident)
        available = compute_available(phys, already_out, printed)
        if requested > available + 1e-9:
            raise HTTPException(status_code=400, detail=STOCK_INSUFFICIENT_MSG)


async def apply_stock_opname(
    db,
    *,
    master_item_id: str,
    counted_unprinted_qty: Any,
    counted_printed_qty: Any,
    note: str = "",
    user: Optional[dict] = None,
) -> Dict[str, Any]:
    """Set physical buckets to counted opname values via a single KOREKSI movement.

    Does not erase history — appends adjustment movement and updates product_stock_state.
    Outstanding SO is unchanged (recomputed on read).
    """
    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")

    state = await get_product_stock_state(db, mid)
    if not state or not (state.get("initialized") or state.get("status") == "INITIALIZED"):
        raise HTTPException(400, "Saldo awal stok fisik produk belum ditetapkan")

    try:
        new_unp = float(counted_unprinted_qty)
        new_pri = float(counted_printed_qty)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "Qty opname harus angka") from e
    if new_unp < 0 or new_pri < 0 or math.isnan(new_unp) or math.isnan(new_pri):
        raise HTTPException(400, "Qty opname tidak boleh negatif")

    old_unp = qty_num(state.get("unprinted_qty"))
    old_pri = qty_num(state.get("printed_qty"))
    old_tot = qty_num(state.get("total_qty"))
    new_tot = new_unp + new_pri
    d_unp = new_unp - old_unp
    d_pri = new_pri - old_pri
    d_tot = new_tot - old_tot

    if abs(d_unp) < 1e-9 and abs(d_pri) < 1e-9:
        return {
            "unchanged": True,
            "stock_state": state,
            "movement": None,
            "message": "Tidak ada selisih opname",
        }

    event_id = f"stock_opname:{mid}:{uuid.uuid4()}"
    stamp = now_iso()
    actor = None
    if isinstance(user, dict):
        actor = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": user.get("name") or user.get("email") or "User",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }

    # Refuse if another durable event is in-flight
    if state.get("pending_event"):
        raise HTTPException(409, "Ada mutasi stok yang sedang berjalan untuk item ini")

    movement = await record_stock_movement(
        db,
        movement_type=MOVEMENT_KOREKSI,
        master_item_id=mid,
        accurate_item_id=state.get("accurate_item_id"),
        transaction_qty=d_tot,
        transaction_uom=state.get("base_uom") or "PCS",
        source_entity="STOCK_OPNAME",
        destination_entity="GUDANG_UTAMA",
        reference_number=f"OPN-{stamp[:10]}",
        reference_type="stock_opname",
        reference_id=event_id,
        description=note or f"Stock Opname: Belum={new_unp}, Sudah={new_pri}",
        delta_unprinted=d_unp,
        delta_printed=d_pri,
        event_key=event_id,
        user=actor,
        now=stamp,
        movement_id=event_id,
        balance_before=old_tot,
        balance_after=new_tot,
    )

    updated = await db[STATE_COLLECTION].find_one_and_update(
        {
            "$and": [
                {"$or": [{"_id": mid}, {"master_item_id": mid}]},
                {"initialized": True},
                {"$or": [
                    {"pending_event": None},
                    {"pending_event": {"$exists": False}},
                ]},
            ]
        },
        {
            "$set": {
                "unprinted_qty": new_unp,
                "printed_qty": new_pri,
                "total_qty": new_tot,
                "updated_at": stamp,
                "last_opname_at": stamp,
                "last_opname_movement_id": event_id,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if not updated:
        # Best-effort: movement already written; surface conflict
        raise HTTPException(409, "Gagal menerapkan opname (konflik konkurensi)")

    # Strip Mongo _id for response
    if isinstance(updated, dict):
        updated = {k: v for k, v in updated.items() if k != "_id"}

    return {
        "unchanged": False,
        "stock_state": updated,
        "movement": movement,
        "delta_unprinted": d_unp,
        "delta_printed": d_pri,
        "delta_total": d_tot,
        "message": "Stock Opname berhasil diterapkan",
    }


async def ensure_v2_physical_settings(db) -> dict:
    """Read-only probe of physical-stock settings for Stock V2 compatibility.

    Deploy/startup MUST NOT silently enable Stock V2 or rewrite app_settings.
    Activation is an explicit ops action via activate_v2_physical_settings()
    (also reachable through PUT /api/settings/physical-stock).
    """
    from services.physical_stock_settings import get_physical_stock_settings

    return await get_physical_stock_settings(db)


async def activate_v2_physical_settings(db, user=None) -> dict:
    """Explicitly enable Stock V2 physical settings (GR in / sales delivery out).

    This is the only path that writes the V2 activation profile into app_settings.
    """
    from services.physical_stock_settings import (
        SETTINGS_KEY,
        get_physical_stock_settings,
        sanitize_settings,
    )

    current = await get_physical_stock_settings(db)
    actor = None
    if isinstance(user, dict):
        actor = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": user.get("name") or user.get("email") or "User",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }
    else:
        actor = {"name": "stock_engine_v2", "email": "system@local"}

    desired = {
        **current,
        "enabled": True,
        "stock_in_trigger": "goods_receipt",
        "stock_out_trigger": "sales_delivery",
        "updated_at": now_iso(),
        "updated_by": actor,
        "stock_engine_version": 2,
    }
    clean = sanitize_settings(desired)
    clean["enabled"] = True
    clean["stock_in_trigger"] = "goods_receipt"
    clean["stock_out_trigger"] = "sales_delivery"
    clean["stock_engine_version"] = 2
    clean["updated_at"] = desired["updated_at"]
    clean["updated_by"] = desired["updated_by"]
    await db.app_settings.update_one(
        {"key": SETTINGS_KEY},
        {"$set": clean},
        upsert=True,
    )
    return clean
