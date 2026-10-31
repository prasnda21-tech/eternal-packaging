"""Sales-order warehouse allocation — recording only, never blocks workflow.

Owner policy: empty / unbalanced / over-allocation / warehouse mismatch on
delivery are WARNINGS. Malformed numbers and unauthorized warehouse refs are
rejected from the saved allocation set (stripped) without failing SO create.

Allocation is commitment metadata on order lines. It never posts ledger or
balance movements.
"""
from __future__ import annotations

import math
import uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ALLOCATION_STATUS_EMPTY = "empty"
ALLOCATION_STATUS_COMPLETE = "complete"
ALLOCATION_STATUS_UNDER = "under"
ALLOCATION_STATUS_OVER = "over"
ALLOCATION_STATUS_INVALID_STRIPPED = "invalid_stripped"

WARNING_EMPTY = "Alokasi gudang belum lengkap"
WARNING_UNDER = "Total alokasi kurang dari qty pesanan"
WARNING_OVER = "Total alokasi melebihi qty pesanan"
WARNING_BAD_QTY = "Qty alokasi tidak valid diabaikan"
WARNING_BAD_WAREHOUSE = "Gudang alokasi tidak sah diabaikan"
WARNING_SERVICE_SKIPPED = "Baris jasa/non-inventory tidak memakai alokasi gudang"
WARNING_STALE_MASTER = "Alokasi dibersihkan karena master/satuan baris berubah"
WARNING_REALIZATION_MISMATCH = "Realisasi pengiriman berbeda dari rencana alokasi"


def _finite_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str) and not str(value).strip():
        return None
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(n) or math.isinf(n):
        return None
    return n


def qty_num(value: Any) -> float:
    n = _finite_number(value)
    if n is None:
        return 0.0
    if n == int(n):
        return int(n)
    return n


def is_inventory_line(line: Optional[dict]) -> bool:
    if not isinstance(line, dict):
        return False
    item_type = str(line.get("item_type") or "").strip().upper()
    if item_type in ("SERVICE", "NON_INVENTORY"):
        return False
    if item_type == "INVENTORY":
        return True
    # Legacy lines without item_type still participate when master_item_id exists.
    return bool(str(line.get("master_item_id") or "").strip())


def new_allocation_id() -> str:
    return f"sa-{uuid.uuid4().hex[:16]}"


def normalize_allocations_for_line(
    raw_allocations: Any,
    *,
    line_id: str,
    ordered_qty: Any,
    unit: str = "PCS",
    branch_id: str = "",
    allowed_warehouses: Optional[Dict[str, dict]] = None,
    master_item_id: str = "",
    require_inventory: bool = True,
    is_inventory: bool = True,
) -> Dict[str, Any]:
    """Normalize one line's warehouse allocations.

    Returns saved allocations + warnings. Never raises for business imbalance.
    Unknown / unauthorized warehouses and non-finite qty are stripped with warnings.
    """
    warnings: List[dict] = []
    ordered = qty_num(ordered_qty)
    unit_norm = str(unit or "PCS").strip() or "PCS"
    lid = str(line_id or "").strip()
    bid = str(branch_id or "").strip()
    mid = str(master_item_id or "").strip()

    if require_inventory and not is_inventory:
        if raw_allocations:
            warnings.append({"code": "SERVICE_SKIPPED", "message": WARNING_SERVICE_SKIPPED, "line_id": lid})
        return {
            "stock_allocations": [],
            "allocated_total": 0.0,
            "ordered_qty": ordered,
            "variance": 0.0 if ordered == 0 else -ordered,
            "status": ALLOCATION_STATUS_EMPTY,
            "warnings": warnings,
            "complete": False,
        }

    merged: Dict[str, Dict[str, Any]] = {}
    raw_list = raw_allocations if isinstance(raw_allocations, list) else []
    for entry in raw_list:
        if not isinstance(entry, dict):
            warnings.append({"code": "BAD_QTY", "message": WARNING_BAD_QTY, "line_id": lid})
            continue
        wid = str(entry.get("warehouse_id") or "").strip()
        qty = _finite_number(entry.get("qty") if entry.get("qty") is not None else entry.get("quantity"))
        if qty is None or qty <= 0:
            warnings.append({"code": "BAD_QTY", "message": WARNING_BAD_QTY, "line_id": lid, "warehouse_id": wid or None})
            continue
        if not wid:
            warnings.append({"code": "BAD_WAREHOUSE", "message": WARNING_BAD_WAREHOUSE, "line_id": lid})
            continue
        if allowed_warehouses is not None:
            wh = allowed_warehouses.get(wid)
            if not wh:
                warnings.append({
                    "code": "BAD_WAREHOUSE",
                    "message": WARNING_BAD_WAREHOUSE,
                    "line_id": lid,
                    "warehouse_id": wid,
                })
                continue
            wh_branch = str(wh.get("branch_id") or "").strip()
            if bid and wh_branch and wh_branch != bid:
                warnings.append({
                    "code": "BAD_WAREHOUSE",
                    "message": WARNING_BAD_WAREHOUSE,
                    "line_id": lid,
                    "warehouse_id": wid,
                })
                continue
        wh_meta = (allowed_warehouses or {}).get(wid) or {}
        prev = merged.get(wid)
        if prev:
            prev["qty"] = qty_num(prev["qty"]) + qty_num(qty)
            continue
        aid = str(entry.get("allocation_id") or "").strip() or new_allocation_id()
        row = {
            "allocation_id": aid,
            "line_id": lid,
            "warehouse_id": wid,
            "branch_id": bid or str(wh_meta.get("branch_id") or "").strip() or None,
            "qty": qty_num(qty),
            "unit": unit_norm,
        }
        if mid:
            row["master_item_id"] = mid
        if wh_meta.get("code"):
            row["warehouse_code"] = wh_meta.get("code")
        if wh_meta.get("name"):
            row["warehouse_name"] = wh_meta.get("name")
        merged[wid] = row

    allocations = list(merged.values())
    allocated_total = qty_num(sum(qty_num(row.get("qty")) for row in allocations))
    variance = qty_num(allocated_total - ordered)

    if not allocations:
        status = ALLOCATION_STATUS_EMPTY
        warnings.append({"code": "ALLOCATION_INCOMPLETE", "message": WARNING_EMPTY, "line_id": lid})
        complete = False
    elif abs(variance) < 1e-9:
        status = ALLOCATION_STATUS_COMPLETE
        complete = True
    elif variance < 0:
        status = ALLOCATION_STATUS_UNDER
        warnings.append({
            "code": "ALLOCATION_UNDER",
            "message": WARNING_UNDER,
            "line_id": lid,
            "ordered_qty": ordered,
            "allocated_total": allocated_total,
            "variance": variance,
        })
        complete = False
    else:
        status = ALLOCATION_STATUS_OVER
        warnings.append({
            "code": "ALLOCATION_OVER",
            "message": WARNING_OVER,
            "line_id": lid,
            "ordered_qty": ordered,
            "allocated_total": allocated_total,
            "variance": variance,
        })
        complete = False

    return {
        "stock_allocations": allocations,
        "allocated_total": allocated_total,
        "ordered_qty": ordered,
        "variance": variance,
        "status": status,
        "warnings": warnings,
        "complete": complete,
    }


def attach_allocations_to_lines(
    lines: Sequence[dict],
    *,
    branch_id: str,
    allowed_warehouses: Optional[Dict[str, dict]] = None,
    raw_by_line_id: Optional[Dict[str, Any]] = None,
    raw_by_index: Optional[Sequence[Any]] = None,
) -> Tuple[List[dict], List[dict], bool]:
    """Mutate copies of lines with stock_allocations. Returns (lines, warnings, all_complete)."""
    out: List[dict] = []
    warnings: List[dict] = []
    all_complete = True
    raw_by_line_id = raw_by_line_id or {}
    for idx, line in enumerate(lines):
        row = dict(line) if isinstance(line, dict) else {}
        lid = str(row.get("line_id") or "").strip()
        inv = is_inventory_line(row)
        raw = None
        if lid and lid in raw_by_line_id:
            raw = raw_by_line_id.get(lid)
        elif raw_by_index is not None and idx < len(raw_by_index):
            raw = raw_by_index[idx]
        elif isinstance(row.get("stock_allocations"), list):
            raw = row.get("stock_allocations")
        result = normalize_allocations_for_line(
            raw,
            line_id=lid,
            ordered_qty=row.get("quantity"),
            unit=str(row.get("unit") or "PCS"),
            branch_id=branch_id,
            allowed_warehouses=allowed_warehouses,
            master_item_id=str(row.get("master_item_id") or ""),
            is_inventory=inv,
        )
        if inv:
            row["stock_allocations"] = result["stock_allocations"]
            row["stock_allocation_status"] = result["status"]
            row["stock_allocation_total"] = result["allocated_total"]
            if not result["complete"]:
                all_complete = False
        else:
            row.pop("stock_allocations", None)
            row.pop("stock_allocation_status", None)
            row.pop("stock_allocation_total", None)
        warnings.extend(result["warnings"])
        out.append(row)
    return out, warnings, all_complete and bool(out)


def clear_stale_allocations_after_line_change(
    before: Optional[dict],
    after: Optional[dict],
) -> Tuple[Optional[List[dict]], List[dict]]:
    """Drop allocations when master_item_id or unit changes."""
    if not isinstance(after, dict):
        return None, []
    if not is_inventory_line(after):
        return [], [{"code": "SERVICE_SKIPPED", "message": WARNING_SERVICE_SKIPPED}]
    prev = before if isinstance(before, dict) else {}
    prev_mid = str(prev.get("master_item_id") or "").strip()
    next_mid = str(after.get("master_item_id") or "").strip()
    prev_unit = str(prev.get("unit") or "").strip().upper()
    next_unit = str(after.get("unit") or "").strip().upper()
    existing = after.get("stock_allocations")
    if prev_mid and next_mid and prev_mid != next_mid:
        return [], [{"code": "STALE_MASTER", "message": WARNING_STALE_MASTER}]
    if prev_unit and next_unit and prev_unit != next_unit:
        return [], [{"code": "STALE_MASTER", "message": WARNING_STALE_MASTER}]
    if isinstance(existing, list):
        return existing, []
    return [], []


def summarize_order_allocation_warnings(lines: Iterable[dict]) -> Dict[str, Any]:
    incomplete_lines = 0
    empty_lines = 0
    for line in lines or []:
        if not is_inventory_line(line):
            continue
        status = str(line.get("stock_allocation_status") or "").strip()
        allocs = line.get("stock_allocations") if isinstance(line.get("stock_allocations"), list) else []
        if status == ALLOCATION_STATUS_COMPLETE and allocs:
            continue
        incomplete_lines += 1
        if not allocs or status == ALLOCATION_STATUS_EMPTY:
            empty_lines += 1
    return {
        "allocation_incomplete": incomplete_lines > 0,
        "incomplete_line_count": incomplete_lines,
        "empty_line_count": empty_lines,
        "message": WARNING_EMPTY if incomplete_lines else None,
    }


async def load_allowed_warehouses_for_branch(db, branch_id: str) -> Dict[str, dict]:
    bid = str(branch_id or "").strip()
    if not bid:
        return {}
    from services.org_locations import warehouse_is_operational

    rows = await db.warehouses.find({"branch_id": bid}, {"_id": 0}).to_list(500)
    out = {}
    for row in rows:
        wid = str(row.get("id") or "").strip()
        if not wid:
            continue
        if not warehouse_is_operational(row):
            continue
        out[wid] = row
    return out


def realization_mismatch_warning(
    *,
    line_id: str,
    planned_warehouse_id: Optional[str],
    actual_warehouse_id: Optional[str],
    planned_remaining: Any = None,
    shipped_qty: Any = None,
) -> Optional[dict]:
    plan = str(planned_warehouse_id or "").strip()
    actual = str(actual_warehouse_id or "").strip()
    if not actual:
        return None
    qty = qty_num(shipped_qty)
    rem = _finite_number(planned_remaining)
    mismatch = False
    if plan and actual and plan != actual:
        mismatch = True
    if rem is not None and qty > rem + 1e-9:
        mismatch = True
    if not mismatch:
        return None
    return {
        "code": "REALIZATION_MISMATCH",
        "message": WARNING_REALIZATION_MISMATCH,
        "line_id": line_id,
        "planned_warehouse_id": plan or None,
        "actual_warehouse_id": actual,
        "planned_remaining": rem,
        "shipped_qty": qty,
    }
