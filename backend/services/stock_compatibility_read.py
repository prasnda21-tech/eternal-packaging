"""Pre-cutover Stock Saat Ini compatibility READ adapter.

When r2_authority_active is FALSE, Stock Saat Ini reads existing
production-compatible sources without writing R2 balances.

Sources (read-only):
- product_stock_state.total_qty → Stock Fisik
- orders + operator_splits → Qty Pesanan / completed
- order.shipments with delivery confirmation / Terkirim → delivered for Belum Terkirim
  (sales_deliveries.status SELESAI/SEBAGIAN is SJ allocation, not customer delivery)

Never mutates product_stock_state, physical_stock_balances, or ledgers.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.inventory_accounting import ITEM_TYPE_SERVICE
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
# Insufficient delivery proof → never invent Belum Terkirim as zero.
BELUM_STATUS_UNVERIFIED = "BELUM_TERVERIFIKASI"
# Alias retained for older clients/tests that still expect the cutover label.
BELUM_STATUS_CUTOVER_PENDING = BELUM_STATUS_UNVERIFIED


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
    """Map PROVEN customer deliveries into delivered qty per master_item_id.

    sales_deliveries.status SELESAI/SEBAGIAN means SJ allocation vs PO remaining,
    not customer receipt — those rows alone never reduce Qty Belum Terkirim.

    Returns (delivered_by_master, provenance_by_master) where provenance is:
    - "confirmed_shipment" when order.shipment has delivery confirmation / Terkirim
    - "order_qty_delivered" when single-line order falls back to order.qty_delivered
    - "unproven" when confirmation exists but line attribution is unsafe
    """
    from services.r2_go_live import (
        _confirmation_line_qtys,
        _delivery_identity_tokens,
        _proven_shipped_qty_for_shipment,
        _resolve_proven_line_attribution,
    )

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
    # (order_id, master_id) that already received confirmed-shipment attribution.
    # qty_delivered fallback is per-PO — never skipped just because another PO
    # already delivered the same master.
    confirmed_order_masters: set = set()
    # deliveries arg kept for API compatibility; SELESAI/SEBAGIAN are not proof.
    _ = deliveries

    def add(mid: str, qty: float, source: str) -> None:
        if not mid or qty <= 0:
            return
        delivered[mid] = _qty_num(delivered.get(mid, 0)) + qty
        prev = provenance.get(mid)
        if prev == "unproven" or source == "unproven":
            provenance[mid] = "unproven"
        elif prev and prev != source:
            provenance[mid] = "confirmed_shipment"
        else:
            provenance[mid] = source

    for order in order_by_id.values():
        order_id = str(order.get("id") or "").strip()
        order_lines = list(order.get("line_items") or [])
        if not order_lines:
            continue
        seen_identities: set = set()
        for sh in order.get("shipments") or []:
            if not isinstance(sh, dict):
                continue
            proven = _proven_shipped_qty_for_shipment(order, sh)
            if proven <= 0:
                continue
            tokens = _delivery_identity_tokens(sh)
            if tokens and any(t in seen_identities for t in tokens):
                continue
            for t in tokens:
                seen_identities.add(t)

            confirmation_lines = _confirmation_line_qtys(
                order, sh, order_line_count=len(order_lines),
            )
            shipment_lines: Dict[int, float] = {}
            if not confirmation_lines:
                line_rows = sh.get("lines") if isinstance(sh.get("lines"), list) else []
                unresolved = False
                for ln in line_rows:
                    if not isinstance(ln, dict):
                        continue
                    qty = _delivery_line_qty(ln)
                    if qty <= 0:
                        continue
                    try:
                        idx = int(ln.get("line_idx"))
                    except (TypeError, ValueError):
                        unresolved = True
                        continue
                    if idx < 0 or idx >= len(order_lines):
                        unresolved = True
                        continue
                    shipment_lines[idx] = float(shipment_lines.get(idx, 0.0)) + qty
                if unresolved and len(order_lines) > 1:
                    for oli in order_lines:
                        om = _line_master_id(oli, owners)
                        if om:
                            unproven_masters[om] = True
                    continue
            sh_tokens = _delivery_identity_tokens(sh)
            attributed, resolve_issues = _resolve_proven_line_attribution(
                order_id=order_id or "(missing-order)",
                identity=sh_tokens[0] if sh_tokens else "(missing-id)",
                proven=proven,
                order_line_count=len(order_lines),
                confirmation_lines=confirmation_lines,
                shipment_lines=shipment_lines,
            )
            if resolve_issues or not attributed:
                for oli in order_lines:
                    om = _line_master_id(oli, owners)
                    if om:
                        unproven_masters[om] = True
                continue
            for idx, qty in attributed.items():
                if idx < 0 or idx >= len(order_lines):
                    continue
                mid = _line_master_id(order_lines[idx], owners)
                add(mid, float(qty), "confirmed_shipment")
                if order_id and mid:
                    confirmed_order_masters.add((order_id, mid))

    # Single-line order.qty_delivered fallback when THIS PO has no confirmed
    # shipment attribution for that master (other POs must not suppress it).
    for order in order_by_id.values():
        order_id = str(order.get("id") or "").strip()
        order_lines = list(order.get("line_items") or [])
        master_ids = [_line_master_id(li, owners) for li in order_lines]
        identified = [m for m in master_ids if m]
        if len(identified) != 1:
            continue
        mid = identified[0]
        if order_id and (order_id, mid) in confirmed_order_masters:
            continue
        qd = _qty_num(order.get("qty_delivered"))
        if qd > 0:
            add(mid, qd, "order_qty_delivered")

    # Ambiguous attribution always wins over partial confirmed totals — never
    # leave READY/confirmed when any shipment for the master is unproven.
    for mid in unproven_masters:
        provenance[mid] = "unproven"

    return delivered, provenance


# Honest fallback — never invent actor from master_items.updated_by.
LAST_STOCK_ACTOR_UNKNOWN = "Tidak tercatat"


def _compatibility_flags() -> dict:
    """Legacy company stock is informational only — never warehouse-valid."""
    return {
        "stock_read_mode": MODE_COMPATIBILITY,
        "physical_qty_scope": "COMPANY",
        "physical_qty_warehouse_valid": False,
        "qty_tersedia_valid": False,
        "qty_tersedia_note": (
            "Qty Tersedia belum valid — stok fisik legacy digabung perusahaan "
            "dan belum direkonsiliasi per gudang (Saldo Awal / Stock Opname R2)."
        ),
        "baseline_status": "unreconciled",
        "baseline_label": "Stok fisik legacy (perusahaan) — bukan saldo per gudang",
    }


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
    flags = _compatibility_flags()
    if belum_status in (BELUM_STATUS_UNVERIFIED, BELUM_STATUS_CUTOVER_PENDING):
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
            # Legacy company stamp is not a posted warehouse movement identity.
            "updated_by_name": LAST_STOCK_ACTOR_UNKNOWN if updated_at else None,
            "belum_terkirim_status": BELUM_STATUS_UNVERIFIED,
            **flags,
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
        "updated_by_name": LAST_STOCK_ACTOR_UNKNOWN if updated_at else None,
        "belum_terkirim_status": BELUM_STATUS_READY,
        **flags,
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
    # Process / jasa Add On (item_type=SERVICE) is work qty, not physical stock.
    # No parent_line_id exists in schema — SERVICE type is the explicit signal.
    type_rows = await db[MASTER_ITEMS_COLLECTION].find(
        {}, {"_id": 0, "id": 1, "item_type": 1},
    ).to_list(5000)
    service_master_ids = {
        str(row.get("id") or "").strip()
        for row in type_rows
        if str(row.get("item_type") or "").strip().upper() == ITEM_TYPE_SERVICE
        and str(row.get("id") or "").strip()
    }
    masters = [
        row for row in masters
        if str(row.get("id") or "").strip() not in service_master_ids
    ]
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
                "bukan saldo per gudang. Belum dipisahkan per cabang. "
                "Filter cabang tidak mengubah qty fisik legacy."
            ),
            "physical_qty_warehouse_valid": False,
            "qty_tersedia_valid": False,
            "compatibility": {
                "physical_source": "product_stock_state.total_qty",
                "order_source": "orders.line_items + operator_splits (print) / PO qty ready (non_print)",
                "delivery_source": (
                    "confirmed order.shipments / delivery_confirmations "
                    "(+ order.qty_delivered single-line fallback); "
                    "not sales_deliveries.status SELESAI/SEBAGIAN"
                ),
                "physical_qty_scope": "COMPANY",
                "physical_qty_warehouse_valid": False,
                "branch_filter_affects_physical_qty": False,
                "qty_tersedia_valid": False,
            },
        }

    units = await _catalog_units(db, masters)
    owners = await _unique_accurate_owners(db)
    orders = await db.orders.find({}, {"_id": 0}).to_list(20000)
    deliveries = await db.sales_deliveries.find(
        {"is_cancelled": {"$ne": True}, "cancelled": {"$ne": True}},
        {"_id": 0},
    ).to_list(20000)
    progress = aggregate_operational_orders(
        orders, owners, exclude_master_ids=service_master_ids,
    )
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
        demand = progress.get(mid) or {
            "ordered_qty": 0.0,
            "completed_qty": 0.0,
            "completion_unproven": False,
        }
        ordered = _qty_num(demand["ordered_qty"])
        completed = _qty_num(demand["completed_qty"])
        delivered = _qty_num(delivered_map.get(mid, 0))
        prov = provenance.get(mid) or ""
        completion_unproven = bool(demand.get("completion_unproven"))
        # Unproven process completion or delivery attribution → never invent siap kirim.
        if (completion_unproven and completed > 0) or (completed > 0 and prov == "unproven"):
            belum_status = BELUM_STATUS_UNVERIFIED
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
            "bukan saldo per gudang. Belum dipisahkan per cabang. "
            "Filter cabang tidak mengubah qty fisik legacy."
        ),
        "compatibility": {
            "physical_source": "product_stock_state.total_qty",
            "order_source": "orders.line_items + operator_splits (print) / PO qty ready (non_print)",
            "delivery_source": (
                "confirmed order.shipments / delivery_confirmations "
                "(+ order.qty_delivered single-line fallback); "
                "not sales_deliveries.status SELESAI/SEBAGIAN"
            ),
            "physical_qty_scope": "COMPANY",
            "physical_qty_warehouse_valid": False,
            "branch_filter_affects_physical_qty": False,
            "qty_tersedia_valid": False,
        },
        "physical_qty_warehouse_valid": False,
        "qty_tersedia_valid": False,
    }
