"""Shared Stock Dapat Dijual (sellable ATP) for SSI catalog and min-stock widget.

Warehouse ATP (when allocations are usable for that warehouse):
  book(W) − remaining_allocation(W)

Branch aggregate:
  Σ book(warehouses) − open_so_commitment(branch)
  where open_so_commitment includes unallocated demand (existing formula).

Never subtract full branch commitment from each warehouse.
Never invent warehouse splits for unallocated demand.
Open SJ is already inside open SO commitment until confirmed delivery —
do not subtract open SJ again from ATP.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from services.stock_allocation import is_inventory_line, qty_num
from services.stock_saat_ini_excel import open_so_commitment_qty

STATUS_READY = "READY"
STATUS_INCOMPLETE = "INCOMPLETE"
REASON_UNALLOCATED_DEMAND = "UNALLOCATED_BRANCH_DEMAND"
REASON_ALLOCATION_INCONSISTENT = "ALLOCATION_INCONSISTENT"
REASON_NO_WAREHOUSE_ATTRIBUTION = "NO_WAREHOUSE_DELIVERY_ATTRIBUTION"


def _line_key(order_id: str, line_id: Optional[str], line_idx: Optional[int]) -> Tuple[str, Optional[str], Optional[int]]:
    return (str(order_id or ""), str(line_id or "").strip() or None, line_idx)


def delivered_by_line_warehouse(
    attributed_deliveries: Iterable[dict],
) -> Dict[Tuple[str, Optional[str], Optional[int], str], float]:
    """qty delivered keyed by (order_id, line_id, line_idx, warehouse_id)."""
    out: Dict[Tuple[str, Optional[str], Optional[int], str], float] = {}
    for raw in attributed_deliveries or []:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("order_id") or "").strip()
        wid = str(raw.get("warehouse_id") or "").strip()
        if not oid:
            continue
        lid = str(raw.get("line_id") or "").strip() or None
        idx = raw.get("line_idx")
        try:
            line_idx = int(idx) if idx is not None and str(idx).strip() != "" else None
        except (TypeError, ValueError):
            line_idx = None
        key = (oid, lid, line_idx, wid)
        out[key] = qty_num(out.get(key, 0)) + qty_num(raw.get("qty"))
    return out


def delivered_by_line(
    attributed_deliveries: Iterable[dict],
) -> Dict[Tuple[str, Optional[str], Optional[int]], float]:
    out: Dict[Tuple[str, Optional[str], Optional[int]], float] = {}
    for raw in attributed_deliveries or []:
        if not isinstance(raw, dict):
            continue
        oid = str(raw.get("order_id") or "").strip()
        if not oid:
            continue
        lid = str(raw.get("line_id") or "").strip() or None
        idx = raw.get("line_idx")
        try:
            line_idx = int(idx) if idx is not None and str(idx).strip() != "" else None
        except (TypeError, ValueError):
            line_idx = None
        key = (oid, lid, line_idx)
        out[key] = qty_num(out.get(key, 0)) + qty_num(raw.get("qty"))
    return out


def _match_delivery_qty(
    by_wh: Dict[Tuple[str, Optional[str], Optional[int], str], float],
    *,
    order_id: str,
    line_id: Optional[str],
    line_idx: Optional[int],
    warehouse_id: str,
) -> float:
    wid = str(warehouse_id or "").strip()
    oid = str(order_id or "").strip()
    lid = str(line_id or "").strip() or None
    if lid is not None:
        return qty_num(by_wh.get((oid, lid, line_idx, wid), by_wh.get((oid, lid, None, wid), 0)))
    if line_idx is not None:
        return qty_num(by_wh.get((oid, None, line_idx, wid), 0))
    return 0.0


def _match_line_delivered(
    by_line: Dict[Tuple[str, Optional[str], Optional[int]], float],
    *,
    order_id: str,
    line_id: Optional[str],
    line_idx: Optional[int],
) -> float:
    oid = str(order_id or "").strip()
    lid = str(line_id or "").strip() or None
    if lid is not None:
        return qty_num(by_line.get((oid, lid, line_idx), by_line.get((oid, lid, None), 0)))
    if line_idx is not None:
        return qty_num(by_line.get((oid, None, line_idx), 0))
    return 0.0


def collect_allocation_commitment(
    orders: Iterable[dict],
    attributed_deliveries: Iterable[dict],
    *,
    exclude_master_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Build per-warehouse remaining allocation and branch unallocated open demand.

    Returns:
      remaining_by_master_warehouse: {master_id: {warehouse_id: qty}}
      unallocated_open_by_master: {master_id: qty}
      incomplete_masters: set of master_ids where warehouse ATP must not claim Aman
      open_commit_by_master: {master_id: qty}  # full branch open SO (allocated+unallocated)
      warnings_by_master: {master_id: [codes]}
    """
    excluded = {str(x).strip() for x in (exclude_master_ids or []) if str(x).strip()}
    by_wh = delivered_by_line_warehouse(attributed_deliveries)
    by_line = delivered_by_line(attributed_deliveries)

    remaining_by_master_warehouse: Dict[str, Dict[str, float]] = {}
    unallocated_open_by_master: Dict[str, float] = {}
    open_commit_by_master: Dict[str, float] = {}
    incomplete_masters: set = set()
    warnings_by_master: Dict[str, List[str]] = {}

    def _warn(mid: str, code: str) -> None:
        bucket = warnings_by_master.setdefault(mid, [])
        if code not in bucket:
            bucket.append(code)

    for order in orders or []:
        if not isinstance(order, dict):
            continue
        if order.get("cancelled") is True:
            continue
        oid = str(order.get("id") or "").strip()
        if not oid:
            continue
        for idx, line in enumerate(order.get("line_items") or []):
            if not isinstance(line, dict):
                continue
            if line.get("cancelled") is True:
                continue
            if not is_inventory_line(line):
                continue
            mid = str(line.get("master_item_id") or "").strip()
            if not mid or mid in excluded:
                continue
            ordered = qty_num(line.get("quantity"))
            if ordered <= 0:
                continue
            lid = str(line.get("line_id") or line.get("id") or "").strip() or None
            delivered_total = _match_line_delivered(by_line, order_id=oid, line_id=lid, line_idx=idx)
            open_commit = open_so_commitment_qty(po_ordered=ordered, so_delivered=delivered_total)
            open_commit_by_master[mid] = qty_num(open_commit_by_master.get(mid, 0)) + open_commit

            allocs = line.get("stock_allocations") if isinstance(line.get("stock_allocations"), list) else []
            remaining_sum = 0.0
            allocated_sum = 0.0
            has_wh_delivery_without_plan = False
            for alloc in allocs:
                if not isinstance(alloc, dict):
                    continue
                wid = str(alloc.get("warehouse_id") or "").strip()
                aq = qty_num(alloc.get("qty"))
                if not wid or aq <= 0:
                    continue
                allocated_sum += aq
                delivered_wh = _match_delivery_qty(
                    by_wh, order_id=oid, line_id=lid, line_idx=idx, warehouse_id=wid,
                )
                # Cap delivered against this allocation so over-delivery on the
                # same warehouse does not invent negative remaining (overage
                # becomes unallocated/mismatch at branch, not negative WH commit).
                rem = max(0.0, aq - delivered_wh)
                remaining_sum += rem
                wh_map = remaining_by_master_warehouse.setdefault(mid, {})
                wh_map[wid] = qty_num(wh_map.get(wid, 0)) + rem

            # Deliveries on warehouses with no plan → incomplete attribution signal.
            for (d_oid, d_lid, d_idx, d_wid), d_qty in by_wh.items():
                if d_oid != oid or qty_num(d_qty) <= 0:
                    continue
                if d_lid and lid and d_lid != lid:
                    continue
                if d_lid is None and d_idx is not None and d_idx != idx:
                    continue
                if d_lid is None and lid and d_idx is None:
                    continue
                planned_wids = {
                    str(a.get("warehouse_id") or "").strip()
                    for a in allocs
                    if isinstance(a, dict) and str(a.get("warehouse_id") or "").strip()
                }
                if d_wid and d_wid not in planned_wids:
                    has_wh_delivery_without_plan = True

            # Unallocated open = open commitment not covered by remaining planned alloc.
            unalloc = max(0.0, open_commit - remaining_sum)
            # If line has no allocations at all, entire open commit is unallocated.
            if not allocs:
                unalloc = open_commit
                incomplete_masters.add(mid)
                _warn(mid, REASON_UNALLOCATED_DEMAND)
            elif abs(allocated_sum - ordered) > 1e-9:
                incomplete_masters.add(mid)
                _warn(mid, REASON_ALLOCATION_INCONSISTENT)
            if unalloc > 1e-9:
                unallocated_open_by_master[mid] = qty_num(
                    unallocated_open_by_master.get(mid, 0)
                ) + unalloc
                incomplete_masters.add(mid)
                _warn(mid, REASON_UNALLOCATED_DEMAND)
            if has_wh_delivery_without_plan:
                incomplete_masters.add(mid)
                _warn(mid, REASON_NO_WAREHOUSE_ATTRIBUTION)

            # Deliveries without warehouse_id on attributed rows (legacy) while
            # allocations exist → cannot fully prove WH remaining.
            bare = 0.0
            for (d_oid, d_lid, d_idx, d_wid), d_qty in by_wh.items():
                if d_oid != oid:
                    continue
                if d_wid:
                    continue
                if d_lid and lid and d_lid != lid:
                    continue
                if d_idx is not None and d_idx != idx and not (d_lid and lid and d_lid == lid):
                    continue
                bare += qty_num(d_qty)
            # also count line deliveries that had empty warehouse key
            if bare > 0 and allocs:
                incomplete_masters.add(mid)
                _warn(mid, REASON_NO_WAREHOUSE_ATTRIBUTION)

    return {
        "remaining_by_master_warehouse": remaining_by_master_warehouse,
        "unallocated_open_by_master": unallocated_open_by_master,
        "open_commit_by_master": open_commit_by_master,
        "incomplete_masters": incomplete_masters,
        "warnings_by_master": warnings_by_master,
    }


def stock_dapat_dijual_warehouse(
    *,
    stock_book: Any,
    remaining_allocation: Any,
    incomplete: bool = False,
) -> Dict[str, Any]:
    if stock_book is None:
        return {
            "qty": None,
            "status": STATUS_INCOMPLETE,
            "reason": REASON_ALLOCATION_INCONSISTENT if incomplete else REASON_UNALLOCATED_DEMAND,
        }
    qty = qty_num(stock_book) - qty_num(remaining_allocation)
    if incomplete:
        return {
            "qty": qty_num(qty),
            "status": STATUS_INCOMPLETE,
            "reason": REASON_UNALLOCATED_DEMAND,
            "provisional": True,
        }
    return {"qty": qty_num(qty), "status": STATUS_READY, "reason": None, "provisional": False}


def stock_dapat_dijual_branch(
    *,
    stock_book: Any,
    open_so_commitment: Any,
) -> Dict[str, Any]:
    """Branch ATP uses full open SO commitment (includes unallocated demand)."""
    if stock_book is None:
        return {"qty": None, "status": STATUS_INCOMPLETE, "reason": REASON_ALLOCATION_INCONSISTENT}
    return {
        "qty": qty_num(qty_num(stock_book) - qty_num(open_so_commitment)),
        "status": STATUS_READY,
        "reason": None,
        "provisional": False,
    }
