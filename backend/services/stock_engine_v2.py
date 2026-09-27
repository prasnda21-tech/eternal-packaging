"""Stock Engine V2 — single source of truth on product_stock_state + stock_movements.

Physical stock (STOCK FISIK) =
  Saldo Awal
  + Penerimaan Barang pembelian / Goods Receipt resmi (VALID / durable movements)
  ± Stock Opname (KOREKSI)
  − Pengiriman yang benar-benar keluar (PENGIRIMAN / sales delivery)

FINAL FORMULA:
  Outstanding SO = Qty SO aktif − Qty delivery confirmation
  Stock Saat Ini = Fisik − Outstanding SO

The physical printed/unprinted buckets are not an order-linked outstanding
allocation. This read model therefore does not manufacture a Belum/Sudah
Dicetak outstanding split.

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
from decimal import Decimal
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastapi import HTTPException

from services.product_stock_state import (
    COLLECTION as STATE_COLLECTION,
    get_product_stock_state,
    initialize_opening_balance,
)
from services.physical_stock_engine import execute_durable_stock_event
from services.purchase_common import money_round
from services.stock_ledger import (
    MOVEMENT_KOREKSI,
    safe_float,
)

# Informational only — never a hard block on order intake / Import PO.
STOCK_INSUFFICIENT_MSG = "Stock tidak mencukupi. Order tetap dapat diproses."
STOCK_INSUFFICIENT_WARNING = STOCK_INSUFFICIENT_MSG


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


def compute_stock_current(physical_qty: Any, outstanding_so_qty: Any) -> float:
    """Stock Saat Ini read value; negative shortages remain visible."""
    current = qty_num(physical_qty) - qty_num(outstanding_so_qty)
    if current == int(current):
        return float(int(current))
    return current


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
    """Outstanding = sum(max(order qty - confirmed qty, 0)) per item."""
    active_orders: List[dict] = []
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
        active_orders.append(order)
        for li in order.get("line_items") or []:
            ident = _line_item_identity(li)
            if not identity_keys(ident):
                continue
            # Establish one canonical product identity across all orders.
            _add({}, aliases, ident, 0)

    deliveries_by_order: Dict[str, List[dict]] = {}
    for delivery in deliveries:
        if delivery.get("is_cancelled") or delivery.get("cancelled"):
            continue
        po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()
        if po_id and po_id in order_by_id:
            deliveries_by_order.setdefault(po_id, []).append(delivery)

    outstanding: Dict[str, float] = {}
    for order in active_orders:
        oid = str(order.get("id") or "").strip()
        ordered_for_order: Dict[str, float] = {}
        for li in order.get("line_items") or []:
            ident = _line_item_identity(li)
            if identity_keys(ident):
                _add(ordered_for_order, aliases, ident, li.get("quantity"))

        delivered_for_order: Dict[str, float] = {}
        for delivery in deliveries_by_order.get(oid, []):
            lines = list(delivery.get("line_items") or delivery.get("lines") or [])
            for ln in lines:
                if not isinstance(ln, dict):
                    continue
                ident = _line_item_identity(ln)
                if not identity_keys(ident):
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
                _add(delivered_for_order, aliases, ident, qty)

        for canon, ordered_qty in ordered_for_order.items():
            delivered_qty = qty_num(delivered_for_order.get(canon, 0))
            remainder = max(0.0, qty_num(ordered_qty) - delivered_qty)
            outstanding[canon] = qty_num(outstanding.get(canon, 0)) + remainder
    return outstanding, aliases


def confirmed_delivery_evidence_from_orders(orders: Iterable[dict]) -> List[dict]:
    """Project confirmed delivery item evidence into aggregate_outstanding_so input.

    Surat Jalan / sales_deliveries creation is not delivery confirmation.
    """
    deliveries: List[dict] = []
    for order in orders:
        order_id = order.get("id")
        order_lines = order.get("line_items") or []
        for confirmation in order.get("delivery_confirmations") or []:
            items = list(confirmation.get("items") or [])
            if not items and len(order_lines) == 1 and confirmation.get("qty") is not None:
                items = [{"line_idx": 0, "qty": confirmation.get("qty")}]
            deliveries.append({
                "po_id": order_id,
                "lines": items,
                "confirmation_id": confirmation.get("id") or confirmation.get("action_id"),
            })
    return deliveries


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
    # Stock Opname corrects authoritative physical total without rewriting
    # transaction-derived production buckets.
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
        initialized = bool(
            state
            and state.get("initialized") is True
            and state.get("status") == "INITIALIZED"
        )
        phys = physical_from_state(state)
        outstanding = _get(outstanding_bucket, outstanding_alias, {
            "master_item_id": mid,
            "accurate_item_id": aid,
            "id": mid,
        })
        printed = phys["printed_qty"]
        polos = compute_stock_polos(phys["physical_qty"], printed)
        stock_current = compute_stock_current(phys["physical_qty"], outstanding)
        correction = (state or {}).get("last_opening_correction") or {}
        changed_by = correction.get("changed_by") or correction.get("corrected_by") or {}
        updated_at = (
            correction.get("changed_at")
            or correction.get("corrected_at")
            or (state or {}).get("updated_at")
            or (state or {}).get("initialized_at")
        )
        last_opname_at = (state or {}).get("last_opname_at")
        rows.append({
            "master_item_id": mid or None,
            "accurate_item_id": aid or None,
            "item_code": it.get("item_code") or "",
            "item_name": it.get("item_name") or "",
            "unit": it.get("unit") or "",
            "initialized": initialized,
            # V2 columns
            "physical_qty": phys["physical_qty"],
            # Physical production buckets. They are not order commitment buckets.
            "unprinted_qty": phys["unprinted_qty"],
            "printed_qty": printed,
            "physical_unprinted_qty": phys["unprinted_qty"],
            "physical_printed_qty": printed,
            # No reliable order-linked print allocation exists.
            "belum_dicetak_qty": None,
            "sudah_dicetak_qty": None,
            "sudah_dicetak_belum_terkirim_qty": None,
            "outstanding_print_breakdown_reliable": False,
            "stock_polos_qty": polos,
            "outstanding_so_qty": outstanding,
            "allocated_qty": outstanding,
            "stock_current": stock_current,
            "available_qty": stock_current,
            # Legacy keys kept for consumers that still read stock_current only
            "qty_po": outstanding,  # semantic shift: now outstanding SO, not full PO qty
            "manual_current_qty": phys["physical_qty"],
            "purchase_qty": 0,
            "purchase_source": "GOODS_RECEIPT",
            "operator_print_qty": 0,
            "manual_purchase_qty": 0,
            "updated_at": updated_at,
            "last_opname_at": last_opname_at,
            "updated_by": (
                changed_by.get("email")
                if isinstance(changed_by, dict) and changed_by
                else (state or {}).get("initialized_by", {}).get("email")
                if isinstance((state or {}).get("initialized_by"), dict)
                else None
            ),
            "updated_by_name": (
                changed_by.get("name")
                if isinstance(changed_by, dict) and changed_by
                else (state or {}).get("initialized_by", {}).get("name")
                if isinstance((state or {}).get("initialized_by"), dict)
                else None
            ),
            "formula": (
                "stock_current = physical - confirmed_delivery_outstanding; "
                "printed_qty is a physical production bucket, not an outstanding allocation"
            ),
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
            "delivery_confirmations.items": 1,
            "delivery_confirmations.qty": 1,
            "delivery_confirmations.id": 1,
            "delivery_confirmations.action_id": 1,
            "line_items.quantity": 1,
            "line_items.accurate_item_id": 1,
            "line_items.master_item_id": 1,
            "line_items.item_id": 1,
        },
    ).to_list(20000)
    deliveries = confirmed_delivery_evidence_from_orders(orders)
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
        "formula": "Stock Dapat Dijual = Stock Fisik − Belum Terkirim Terkonfirmasi",
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
) -> List[str]:
    """Return informational low-stock warnings for SO create / Import PO.

    Stock shortage (zero / negative / qty > available) must NOT block order intake.
    Callers may surface the returned warnings to the UI; they must not raise on them.

    For revise, exclude_order_id removes that order's current outstanding from the
    denominator so the revision is compared against free stock + its own prior reservation.

    V2 availability is authoritative only while physical_stock.enabled is true.
    While OFF, this gate is a no-op so missing product_stock_state cannot reject
    legitimate Accurate / new sales orders.
    """
    from services.physical_stock_settings import get_physical_stock_settings

    settings = await get_physical_stock_settings(db)
    if not bool(settings.get("enabled")):
        return []

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
            "status_label": 1,
            "delivery_confirmations.items": 1,
            "delivery_confirmations.qty": 1,
            "delivery_confirmations.id": 1,
            "delivery_confirmations.action_id": 1,
            "line_items.quantity": 1,
            "line_items.accurate_item_id": 1,
            "line_items.master_item_id": 1,
            "line_items.item_id": 1,
        },
    ).to_list(20000)
    if exclude_order_id:
        oid = str(exclude_order_id)
        orders = [o for o in orders if str(o.get("id")) != oid]
    deliveries = confirmed_delivery_evidence_from_orders(orders)
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

    shortfall = False
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
            shortfall = True
            break

    if shortfall:
        return [STOCK_INSUFFICIENT_WARNING]
    return []


async def apply_stock_opname(
    db,
    *,
    master_item_id: str,
    stock_physical_counted: Any,
    outstanding_so_qty: Any,
    note: str = "",
    user: Optional[dict] = None,
    client_request_id: Optional[str] = None,
    valuation_context: Optional[dict] = None,
) -> Dict[str, Any]:
    """Apply an absolute physical warehouse count as SALDO_AWAL or KOREKSI.

    Does not erase history. The first physical count establishes exactly one
    official opening balance; later counts append adjustments.
    Outstanding Sales Order reservations are read-only and remain unchanged.
    The physical printed bucket is preserved; unprinted becomes its physical
    residual so total = unprinted + printed remains valid.
    """
    mid = str(master_item_id or "").strip()
    if not mid:
        raise HTTPException(400, "master_item_id wajib diisi")

    try:
        counted_physical = float(stock_physical_counted)
        outstanding = float(outstanding_so_qty)
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "Qty opname harus angka") from e
    values = (counted_physical, outstanding)
    if any(v < 0 or math.isnan(v) or math.isinf(v) for v in values):
        raise HTTPException(400, "Qty opname tidak boleh negatif")
    target_physical = counted_physical

    request_id = str(client_request_id or "").strip() or str(uuid.uuid4())
    event_id = f"stock_opname:{mid}:{request_id}"
    existing = await db.stock_movements.find_one(
        {"$or": [{"event_key": event_id}, {"id": event_id}, {"_id": event_id}]},
        {"_id": 0},
    )
    if existing:
        metadata = existing.get("change_metadata") or existing
        return {
            "unchanged": False,
            "idempotent": True,
            "opname_action": "KOREKSI",
            "stock_state": await get_product_stock_state(db, mid),
            "movement": existing,
            "stock_physical_before": metadata.get("stock_physical_before", existing.get("balance_before")),
            "stock_physical_counted": metadata.get("stock_physical_counted", existing.get("balance_after")),
            "difference_qty": metadata.get("difference_qty", existing.get("delta")),
            "effective_hpp_snapshot": metadata.get("effective_hpp_snapshot"),
            "hpp_source": metadata.get("hpp_source"),
            "hpp_status_snapshot": metadata.get("hpp_status_snapshot"),
            "estimated_difference_value": metadata.get("estimated_difference_value"),
            "estimated_value_is_informational": True,
            "valuation_adjustment_required": bool(metadata.get("valuation_adjustment_required")),
            "message": "Stock Opname sudah tercatat",
        }

    state = await get_product_stock_state(db, mid)
    initialized = bool(
        state
        and state.get("initialized") is True
        and state.get("status") == "INITIALIZED"
    )
    # Preserve printed stock; reconcile unprinted as physical residual.
    new_pri = qty_num(state.get("printed_qty")) if initialized else 0.0
    new_unp = target_physical - new_pri if initialized else target_physical
    if new_unp < -1e-9:
        raise HTTPException(
            409,
            "Stock Fisik hasil hitung lebih kecil dari bucket Sudah Dicetak. "
            "Rekonsiliasi bucket diperlukan sebelum opname.",
        )
    new_unp = max(new_unp, 0.0)
    if (
        initialized
        and state.get("initialization_source") == "STOCK_OPNAME"
        and state.get("initialization_request_id") == request_id
    ):
        opening_id = str(state.get("opening_movement_id") or "")
        opening = await db.stock_movements.find_one(
            {"$or": [{"event_key": opening_id}, {"id": opening_id}, {"_id": opening_id}]},
            {"_id": 0},
        )
        return {
            "unchanged": False,
            "idempotent": True,
            "opname_action": "SALDO_AWAL",
            "stock_state": state,
            "movement": opening,
            "message": "Stock Opname pertama sudah menetapkan SALDO_AWAL",
        }
    if not initialized:
        opening = await initialize_opening_balance(
            db,
            mid,
            {
                "unprinted_qty": new_unp,
                "printed_qty": new_pri,
                "total_qty": new_unp + new_pri,
                "initialization_source": "STOCK_OPNAME",
                "client_request_id": request_id,
                "note": note or "Saldo awal resmi dari Stock Opname pertama",
            },
            user=user,
        )
        return {
            "unchanged": False,
            "idempotent": False,
            "opname_action": "SALDO_AWAL",
            "stock_state": opening.get("stock_state"),
            "movement": opening.get("movement"),
            "delta_unprinted": new_unp,
            "delta_printed": new_pri,
            "delta_total": new_unp + new_pri,
            "stock_physical_before": 0.0,
            "stock_physical_counted": target_physical,
            "difference_qty": target_physical,
            "effective_hpp_snapshot": None,
            "hpp_source": None,
            "hpp_status_snapshot": None,
            "estimated_difference_value": None,
            "estimated_value_is_informational": True,
            "valuation_adjustment_required": False,
            "message": "Stock Opname pertama berhasil menetapkan SALDO_AWAL",
        }

    old_unp = qty_num(state.get("unprinted_qty"))
    old_pri = qty_num(state.get("printed_qty"))
    old_tot = qty_num(state.get("total_qty"))
    new_tot = target_physical
    d_unp = new_unp - old_unp
    d_pri = 0.0
    d_tot = new_tot - old_tot

    if valuation_context is None:
        from services.inventory_valuation import build_inventory_valuation_rows
        valuation = await build_inventory_valuation_rows(db)
        valuation_context = next(
            (
                row for row in valuation.get("rows") or []
                if str(row.get("master_item_id") or "") == mid
            ),
            None,
        )
    hpp_snapshot = None
    hpp_source = None
    hpp_status_snapshot = (
        valuation_context.get("hpp_status")
        if valuation_context
        else None
    )
    if (
        valuation_context
        and valuation_context.get("current_hpp") is not None
        and qty_num(valuation_context.get("current_hpp")) > 0
    ):
        hpp_snapshot = money_round(valuation_context.get("current_hpp"))
        hpp_source = valuation_context.get("hpp_source")
    estimated_difference_value = (
        money_round(Decimal(str(d_tot)) * Decimal(str(hpp_snapshot)))
        if hpp_snapshot is not None
        else None
    )
    valuation_adjustment_required = bool(
        abs(d_tot) >= 1e-9
        and hpp_source == "PURCHASING_MOVING_AVERAGE"
    )

    if abs(d_tot) < 1e-9:
        return {
            "unchanged": True,
            "opname_action": "KOREKSI",
            "stock_state": state,
            "movement": None,
            "stock_physical_before": old_tot,
            "stock_physical_counted": new_tot,
            "difference_qty": 0.0,
            "effective_hpp_snapshot": hpp_snapshot,
            "hpp_source": hpp_source,
            "hpp_status_snapshot": hpp_status_snapshot,
            "estimated_difference_value": 0.0 if hpp_snapshot is not None else None,
            "estimated_value_is_informational": True,
            "valuation_adjustment_required": False,
            "message": "Tidak ada selisih opname",
        }

    stamp = now_iso()
    actor = None
    if isinstance(user, dict):
        actor = {
            "id": str(user.get("id") or user.get("_id") or ""),
            "name": user.get("name") or user.get("email") or "User",
            "email": user.get("email") or "",
            "role": user.get("role") or "",
        }

    movement = await execute_durable_stock_event(
        db,
        event_id=event_id,
        movement_type=MOVEMENT_KOREKSI,
        master_item_id=mid,
        transaction_qty=d_tot,
        transaction_uom=state.get("base_uom") or "PCS",
        source_entity="STOCK_OPNAME",
        destination_entity="GUDANG_UTAMA",
        reference_number=f"OPN-{stamp[:10]}",
        reference_type="stock_opname",
        reference_id=event_id,
        description=note or f"Stock Opname: Stock Fisik hasil hitung={counted_physical}",
        user=actor,
        now=stamp,
        delta_total_override=d_tot,
        delta_unprinted_override=d_unp,
        delta_printed_override=d_pri,
        expected_total_qty=state.get("total_qty"),
        expected_unprinted_qty=state.get("unprinted_qty"),
        expected_printed_qty=state.get("printed_qty"),
        change_metadata={
            "old_qty": old_tot,
            "new_qty": new_tot,
            "difference_qty": d_tot,
            "stock_physical_before": old_tot,
            "stock_physical_counted": new_tot,
            "changed_by": actor,
            "changed_at": stamp,
            "change_note": note,
            "client_request_id": request_id,
            "outstanding_so_snapshot": outstanding,
            "effective_hpp_snapshot": hpp_snapshot,
            "hpp_source": hpp_source,
            "hpp_status_snapshot": hpp_status_snapshot,
            "estimated_difference_value": estimated_difference_value,
            "estimated_value_is_informational": True,
            "valuation_adjustment_required": valuation_adjustment_required,
        },
    )
    await db[STATE_COLLECTION].update_one(
        {"$or": [{"_id": mid}, {"master_item_id": mid}]},
        {"$set": {
            "last_opname_at": stamp,
            "last_opname_movement_id": event_id,
            "last_opname_by": actor,
        }},
    )
    updated = await get_product_stock_state(db, mid)

    # Strip Mongo _id for response
    if isinstance(updated, dict):
        updated = {k: v for k, v in updated.items() if k != "_id"}

    return {
        "unchanged": False,
        "opname_action": "KOREKSI",
        "stock_state": updated,
        "movement": movement,
        "delta_unprinted": d_unp,
        "delta_printed": d_pri,
        "delta_total": d_tot,
        "stock_physical_before": old_tot,
        "stock_physical_counted": new_tot,
        "difference_qty": d_tot,
        "effective_hpp_snapshot": hpp_snapshot,
        "hpp_source": hpp_source,
        "hpp_status_snapshot": hpp_status_snapshot,
        "estimated_difference_value": estimated_difference_value,
        "estimated_value_is_informational": True,
        "valuation_adjustment_required": valuation_adjustment_required,
        "message": "Stock Opname berhasil diterapkan",
    }


async def ensure_v2_physical_settings(db) -> dict:
    """Read physical-stock settings without changing the mutation gate."""
    from services.physical_stock_settings import get_physical_stock_settings

    return await get_physical_stock_settings(db)
