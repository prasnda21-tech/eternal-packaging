"""Pre-cutover Stock Saat Ini compatibility READ adapter.

When r2_authority_active is FALSE, Stock Saat Ini reads existing
production-compatible sources without writing R2 balances.

Sources (read-only):
- product_stock_state.total_qty → Stock Fisik
- orders + operator_splits → Qty Pesanan / completed
- sales_deliveries line qty → delivered for Belum Terkirim when identity is proven

Never mutates product_stock_state, physical_stock_balances, or ledgers.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.physical_stock import (
    MASTER_ITEMS_COLLECTION,
    _catalog_page,
    _catalog_units,
    _line_master_id,
    _order_cancelled,
    _qty_num,
    _unique_accurate_owners,
    aggregate_operational_orders,
    operational_availability,
)

MODE_COMPATIBILITY = "compatibility"
MODE_R2 = "r2"

BELUM_STATUS_READY = "READY"
BELUM_STATUS_CUTOVER_PENDING = "CUTOVER_PENDING"


def _delivery_line_qty(line: dict) -> float:
    for key in ("qty", "quantity", "shipped_qty", "delivered_qty"):
        if line.get(key) is not None:
            return max(_qty_num(line.get(key)), 0.0)
    return 0.0


def aggregate_legacy_delivered_qty(
    orders: Iterable[dict],
    deliveries: Iterable[dict],
    accurate_owners: Optional[Dict[str, str]] = None,
) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Map sales_deliveries into delivered qty per master_item_id.

    Returns (delivered_by_master, provenance_by_master) where provenance is:
    - "sales_delivery_line" when line identity (or line_idx→order line) resolved
    - "order_qty_delivered" when single-line order falls back to order.qty_delivered
    - "unproven" when delivery rows exist but cannot be attributed safely
    """
    owners = accurate_owners or {}
    order_by_id: Dict[str, dict] = {}
    for order in orders:
        if _order_cancelled(order):
            continue
        oid = str(order.get("id") or "").strip()
        if oid:
            order_by_id[oid] = order

    delivered: Dict[str, float] = {}
    provenance: Dict[str, str] = {}
    unproven_masters: Dict[str, bool] = {}

    def add(mid: str, qty: float, source: str) -> None:
        if not mid or qty <= 0:
            return
        delivered[mid] = _qty_num(delivered.get(mid, 0)) + qty
        prev = provenance.get(mid)
        if prev == "unproven" or source == "unproven":
            provenance[mid] = "unproven"
        elif prev and prev != source:
            provenance[mid] = "sales_delivery_line"
        else:
            provenance[mid] = source

    for delivery in deliveries:
        if delivery.get("is_cancelled") or delivery.get("cancelled"):
            continue
        po_id = str(delivery.get("po_id") or delivery.get("order_id") or "").strip()
        order = order_by_id.get(po_id)
        lines = list(delivery.get("line_items") or delivery.get("lines") or [])
        if not lines:
            # Header-only delivery: only safe for single-master orders via qty on header
            if order is None:
                continue
            order_lines = list(order.get("line_items") or [])
            master_ids = [_line_master_id(li, owners) for li in order_lines]
            identified = [m for m in master_ids if m]
            header_qty = _qty_num(
                delivery.get("total_qty")
                or delivery.get("qty")
                or delivery.get("quantity")
            )
            if len(identified) == 1 and header_qty > 0:
                add(identified[0], header_qty, "sales_delivery_line")
            elif identified:
                for mid in set(identified):
                    unproven_masters[mid] = True
            continue

        for ln in lines:
            if not isinstance(ln, dict):
                continue
            mid = _line_master_id(ln, owners)
            if not mid and order is not None:
                try:
                    idx = int(ln.get("line_idx"))
                except (TypeError, ValueError):
                    idx = None
                order_lines = order.get("line_items") or []
                if idx is not None and 0 <= idx < len(order_lines):
                    mid = _line_master_id(order_lines[idx], owners)
            qty = _delivery_line_qty(ln)
            if mid and qty > 0:
                add(mid, qty, "sales_delivery_line")
            elif qty > 0 and order is not None:
                # Delivery line qty present but identity unknown → mark order masters unproven
                for oli in order.get("line_items") or []:
                    om = _line_master_id(oli, owners)
                    if om:
                        unproven_masters[om] = True

    # Single-line order.qty_delivered fallback when no sales_delivery lines contributed
    for order in order_by_id.values():
        order_lines = list(order.get("line_items") or [])
        master_ids = [_line_master_id(li, owners) for li in order_lines]
        identified = [m for m in master_ids if m]
        if len(identified) != 1:
            continue
        mid = identified[0]
        if mid in delivered:
            continue
        qd = _qty_num(order.get("qty_delivered"))
        if qd > 0:
            add(mid, qd, "order_qty_delivered")

    for mid in unproven_masters:
        if mid not in delivered:
            provenance[mid] = "unproven"
        elif provenance.get(mid) != "sales_delivery_line":
            provenance[mid] = "unproven"

    return delivered, provenance


def compatibility_row(
    *,
    master: dict,
    physical_qty: float,
    ordered_qty: float,
    completed_qty: float,
    delivered_qty: float,
    belum_status: str,
    updated_at: Optional[str],
    base_unit: str,
) -> dict:
    if belum_status == BELUM_STATUS_CUTOVER_PENDING:
        # Do not invent Belum Terkirim. Qty Tersedia excludes only Qty Pesanan.
        buckets = operational_availability(
            physical_qty=physical_qty,
            ordered_qty=ordered_qty,
            completed_qty=completed_qty,
            r2_delivered_qty=completed_qty,  # treat completed as satisfied for formula floor only
        )
        # Override: expose null belum and available without fabricated belum
        qty_pesanan = buckets["qty_pesanan"]
        return {
            "master_item_id": master.get("id"),
            "item_code": master.get("item_code") or "",
            "item_name": master.get("item_name") or master.get("name") or "",
            "accurate_item_id": str(master.get("accurate_item_id") or ""),
            "base_unit": base_unit or "PCS",
            "physical_qty": buckets["physical_qty"],
            "qty_pesanan": qty_pesanan,
            "qty_belum_terkirim": None,
            "qty_tersedia": buckets["physical_qty"] - qty_pesanan,
            "updated_at": updated_at,
            "belum_terkirim_status": BELUM_STATUS_CUTOVER_PENDING,
            "stock_read_mode": MODE_COMPATIBILITY,
        }

    buckets = operational_availability(
        physical_qty=physical_qty,
        ordered_qty=ordered_qty,
        completed_qty=completed_qty,
        r2_delivered_qty=delivered_qty,
    )
    return {
        "master_item_id": master.get("id"),
        "item_code": master.get("item_code") or "",
        "item_name": master.get("item_name") or master.get("name") or "",
        "accurate_item_id": str(master.get("accurate_item_id") or ""),
        "base_unit": base_unit or "PCS",
        "physical_qty": buckets["physical_qty"],
        "qty_pesanan": buckets["qty_pesanan"],
        "qty_belum_terkirim": buckets["qty_belum_terkirim"],
        "qty_tersedia": buckets["qty_tersedia"],
        "updated_at": updated_at,
        "belum_terkirim_status": BELUM_STATUS_READY,
        "stock_read_mode": MODE_COMPATIBILITY,
    }


async def list_compatibility_stock_catalog(
    db,
    *,
    q: Optional[str] = None,
    page: int = 1,
    limit: int = 50,
) -> dict:
    """Read-only pre-cutover Stock Saat Ini from legacy-compatible sources."""
    item_query: Dict[str, Any] = {}
    text = str(q or "").strip()
    if text:
        pattern = re.escape(text)
        item_query["$or"] = [
            {"item_code": {"$regex": pattern, "$options": "i"}},
            {"item_name": {"$regex": pattern, "$options": "i"}},
            {"name": {"$regex": pattern, "$options": "i"}},
            {"accurate_item_id": {"$regex": pattern, "$options": "i"}},
        ]
    masters = await db[MASTER_ITEMS_COLLECTION].find(item_query, {"_id": 0}).sort(
        [("item_name", 1), ("id", 1)]
    ).to_list(1000)
    safe_page, safe_limit = _catalog_page(page, limit)
    if not masters:
        return {
            "items": [],
            "total": 0,
            "page": safe_page,
            "limit": safe_limit,
            "page_count": 0,
            "master_item_count": 0,
            "warehouse_count": 0,
            "stock_read_mode": MODE_COMPATIBILITY,
            "r2_authority_active": False,
            "physical_qty_scope": "COMPANY",
            "branch_filter_affects_physical_qty": False,
            "physical_qty_note": (
                "Qty fisik legacy digabung perusahaan (product_stock_state); "
                "belum dipisahkan per cabang. Filter cabang tidak mengubah qty fisik legacy."
            ),
            "compatibility": {
                "physical_source": "product_stock_state.total_qty",
                "order_source": "orders.line_items + operator_splits (print) / PO qty ready (non_print)",
                "delivery_source": "sales_deliveries (+ order.qty_delivered single-line fallback)",
                "physical_qty_scope": "COMPANY",
                "branch_filter_affects_physical_qty": False,
            },
        }

    units = await _catalog_units(db, masters)
    owners = await _unique_accurate_owners(db)
    orders = await db.orders.find({}, {"_id": 0}).to_list(20000)
    deliveries = await db.sales_deliveries.find(
        {"is_cancelled": {"$ne": True}, "cancelled": {"$ne": True}},
        {"_id": 0},
    ).to_list(20000)
    progress = aggregate_operational_orders(orders, owners)
    delivered_map, provenance = aggregate_legacy_delivered_qty(orders, deliveries, owners)

    state_by_mid: Dict[str, dict] = {}
    async for doc in db.product_stock_state.find({}, {"_id": 0}):
        mid = str(doc.get("master_item_id") or doc.get("id") or "").strip()
        if mid:
            state_by_mid[mid] = doc

    rows = []
    for master in masters:
        mid = master.get("id")
        state = state_by_mid.get(mid) or {}
        initialized = bool(state.get("initialized") or state.get("status") == "INITIALIZED")
        physical = _qty_num(state.get("total_qty")) if initialized else 0.0
        demand = progress.get(mid) or {"ordered_qty": 0.0, "completed_qty": 0.0}
        ordered = _qty_num(demand["ordered_qty"])
        completed = _qty_num(demand["completed_qty"])
        delivered = _qty_num(delivered_map.get(mid, 0))
        prov = provenance.get(mid) or ""
        # Unproven delivery attribution with open completion → do not invent Belum Terkirim
        if completed > 0 and prov == "unproven":
            belum_status = BELUM_STATUS_CUTOVER_PENDING
        else:
            belum_status = BELUM_STATUS_READY
        rows.append(
            compatibility_row(
                master=master,
                physical_qty=physical,
                ordered_qty=ordered,
                completed_qty=completed,
                delivered_qty=delivered,
                belum_status=belum_status,
                updated_at=state.get("updated_at"),
                base_unit=units.get(mid) or "PCS",
            )
        )

    total = len(rows)
    start = (safe_page - 1) * safe_limit
    page_count = (total + safe_limit - 1) // safe_limit if total else 0
    return {
        "items": rows[start:start + safe_limit],
        "total": total,
        "page": safe_page,
        "limit": safe_limit,
        "page_count": page_count,
        "master_item_count": total,
        "warehouse_count": 0,
        "stock_read_mode": MODE_COMPATIBILITY,
        "r2_authority_active": False,
        "physical_qty_scope": "COMPANY",
        "branch_filter_affects_physical_qty": False,
        "physical_qty_note": (
            "Qty fisik legacy digabung perusahaan (product_stock_state); "
            "belum dipisahkan per cabang. Filter cabang tidak mengubah qty fisik legacy."
        ),
        "compatibility": {
            "physical_source": "product_stock_state.total_qty",
            "order_source": "orders.line_items + operator_splits (print) / PO qty ready (non_print)",
            "delivery_source": "sales_deliveries (+ order.qty_delivered single-line fallback)",
            "physical_qty_scope": "COMPANY",
            "branch_filter_affects_physical_qty": False,
        },
    }
