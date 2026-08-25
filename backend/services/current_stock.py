"""Stock Saat Ini — quantity-only, per Master Item.

stock_current = po_qty - (manual_current_qty + purchase_qty) - operator_print_qty

Identity is master_item_id and/or accurate_item_id. Never match by name.
Cancelled POs are excluded. Operator qty is the snapshot
`operator_splits.qty_completed` (re-save overwrites; no double count).
Pembelian module has no transaction data → MANUAL FALLBACK field.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Optional, Tuple

from quantity_rules import is_print_line

COLLECTION = "item_stock_manual"
PURCHASE_SOURCE = "MANUAL_FALLBACK"
ORDER_PROJECTION = {
    "_id": 0,
    "cancelled": 1,
    "line_items.quantity": 1,
    "line_items.accurate_item_id": 1,
    "line_items.master_item_id": 1,
    "line_items.production_process": 1,
    "operator_splits.qty_completed": 1,
    "operator_splits.line_idx": 1,
}
SORT_KEYS = {
    "name": "item_name",
    "item_name": "item_name",
    "qty_po": "qty_po",
    "operator_print_qty": "operator_print_qty",
    "stock_current": "stock_current",
}


def qty_num(v: Any) -> float:
    try:
        if v is None or str(v).strip() == "":
            return 0
        n = float(v)
        if math.isnan(n) or math.isinf(n):
            return 0
        if n == int(n):
            return int(n)
        return n
    except (TypeError, ValueError):
        return 0


def parse_manual_qty(v: Any) -> float:
    """Validate user input: numeric, >= 0, not NaN. Follows existing int qty
    convention when the value is whole; otherwise keeps a finite decimal."""
    if v is None or (isinstance(v, str) and not str(v).strip()):
        raise ValueError("Qty wajib diisi")
    if isinstance(v, bool):
        raise ValueError("Qty harus angka")
    try:
        n = float(v)
    except (TypeError, ValueError) as e:
        raise ValueError("Qty harus angka") from e
    if math.isnan(n) or math.isinf(n):
        raise ValueError("Qty tidak valid")
    if n < 0:
        raise ValueError("Qty tidak boleh negatif")
    if n == int(n):
        return int(n)
    return n


def compute_stock_current(
    po_qty: Any,
    manual_current_qty: Any,
    purchase_qty: Any,
    operator_print_qty: Any,
) -> float:
    """Authoritative formula. Negative values are preserved (never clamped)."""
    stock = (
        qty_num(po_qty)
        - (qty_num(manual_current_qty) + qty_num(purchase_qty))
        - qty_num(operator_print_qty)
    )
    if isinstance(stock, float) and stock == int(stock):
        return int(stock)
    return stock


def item_identity(doc: dict) -> Optional[Tuple[str, str]]:
    """Stable identity: prefer accurate_item_id, else master_item_id / id."""
    aid = str(doc.get("accurate_item_id") or "").strip()
    if aid:
        return ("accurate_item_id", aid)
    mid = str(doc.get("master_item_id") or doc.get("id") or "").strip()
    if mid:
        return ("master_item_id", mid)
    return None


def identity_keys(doc: dict) -> List[str]:
    keys = []
    aid = str(doc.get("accurate_item_id") or "").strip()
    mid = str(doc.get("master_item_id") or doc.get("id") or "").strip()
    if aid:
        keys.append(f"a:{aid}")
    if mid:
        keys.append(f"m:{mid}")
    return keys


def canonical_key(doc: dict) -> Optional[str]:
    keys = identity_keys(doc)
    return keys[0] if keys else None


def _add(bucket: Dict[str, float], aliases: Dict[str, str], doc: dict, amount: Any) -> None:
    n = qty_num(amount)
    if n == 0 and amount in (None, "", 0, "0"):
        # still register identity so later lookups merge
        pass
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
        bucket.setdefault(canon, 0)
    for k in keys:
        aliases[k] = canon
    bucket[canon] = qty_num(bucket.get(canon, 0)) + n


def _get(bucket: Dict[str, float], aliases: Dict[str, str], doc: dict) -> float:
    for k in identity_keys(doc):
        canon = aliases.get(k)
        if canon is not None:
            return qty_num(bucket.get(canon, 0))
    return 0


def aggregate_po_qty(orders: Iterable[dict]) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Sum PO line `quantity` per item identity. Skip cancelled. Skip lines
    without accurate_item_id / master_item_id (no name matching)."""
    bucket: Dict[str, float] = {}
    aliases: Dict[str, str] = {}
    for order in orders:
        if order.get("cancelled"):
            continue
        for li in (order.get("line_items") or []):
            if not identity_keys(li):
                continue
            _add(bucket, aliases, li, li.get("quantity"))
    return bucket, aliases


def _print_lines(order: dict) -> List[Tuple[int, dict]]:
    out = []
    for i, li in enumerate(order.get("line_items") or []):
        if is_print_line(li) and identity_keys(li):
            out.append((i, li))
    return out


def aggregate_operator_print_qty(orders: Iterable[dict]) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Sum operator_splits.qty_completed (snapshot) per item.

    Splits with line_idx map to that line. Unassigned splits attach only when
    the PO has exactly one identified print line — never by item name.
    Re-saving the same snapshot does not double-count.
    """
    bucket: Dict[str, float] = {}
    aliases: Dict[str, str] = {}
    for order in orders:
        if order.get("cancelled"):
            continue
        lines = list(order.get("line_items") or [])
        assigned_idx = set()
        for s in (order.get("operator_splits") or []):
            if s.get("line_idx") is None or str(s.get("line_idx")).strip() == "":
                continue
            try:
                idx = int(s.get("line_idx"))
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(lines):
                continue
            li = lines[idx]
            if not identity_keys(li):
                continue
            assigned_idx.add(idx)
            _add(bucket, aliases, li, s.get("qty_completed"))
        unassigned = [
            s for s in (order.get("operator_splits") or [])
            if s.get("line_idx") is None or str(s.get("line_idx")).strip() == ""
        ]
        print_ids = _print_lines(order)
        if unassigned and len(print_ids) == 1:
            _li = print_ids[0][1]
            for s in unassigned:
                _add(bucket, aliases, _li, s.get("qty_completed"))
    return bucket, aliases


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


def matches_search(item: dict, q: str) -> bool:
    needle = (q or "").strip().lower()
    if not needle:
        return True
    code = str(item.get("item_code") or "").lower()
    name = str(item.get("item_name") or "").lower()
    return needle in code or needle in name


def build_stock_rows(
    master_items: Iterable[dict],
    orders: Iterable[dict],
    manuals: Iterable[dict],
) -> List[dict]:
    order_list = list(orders)
    po_bucket, po_alias = aggregate_po_qty(order_list)
    print_bucket, print_alias = aggregate_operator_print_qty(order_list)
    man_by_key: Dict[str, dict] = {}
    man_alias: Dict[str, str] = {}
    for m in manuals:
        keys = identity_keys(m)
        if not keys:
            continue
        canon = keys[0]
        for k in keys:
            man_alias[k] = canon
        man_by_key[canon] = m

    rows = []
    for it in master_items:
        keys = identity_keys(it)
        if not keys:
            continue
        po_qty = _get(po_bucket, po_alias, it)
        op_qty = _get(print_bucket, print_alias, it)
        man = None
        for k in keys:
            canon = man_alias.get(k)
            if canon and canon in man_by_key:
                man = man_by_key[canon]
                break
            if k in man_by_key:
                man = man_by_key[k]
                break
        manual_current = qty_num((man or {}).get("manual_current_qty"))
        purchase = qty_num((man or {}).get("manual_purchase_qty"))
        stock = compute_stock_current(po_qty, manual_current, purchase, op_qty)
        updated_at = (man or {}).get("updated_at")
        updated_by = (man or {}).get("updated_by")
        updated_by_name = (man or {}).get("updated_by_name")
        rows.append({
            "master_item_id": it.get("id") or it.get("master_item_id"),
            "accurate_item_id": it.get("accurate_item_id"),
            "item_code": it.get("item_code") or "",
            "item_name": it.get("item_name") or "",
            "unit": it.get("unit") or "",
            "qty_po": po_qty,
            "manual_current_qty": manual_current,
            "purchase_qty": purchase,
            "purchase_source": PURCHASE_SOURCE,
            "operator_print_qty": op_qty,
            "stock_current": stock,
            "updated_at": updated_at,
            "updated_by": updated_by,
            "updated_by_name": updated_by_name,
        })
    return rows


def filter_sort_page(
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
        "purchase_source": PURCHASE_SOURCE,
        "formula": "qty_po - (manual_current_qty + purchase_qty) - operator_print_qty",
        "qty_po_source": "orders.line_items.quantity (cancelled excluded)",
        "operator_print_source": "operator_splits.qty_completed snapshot per line_idx",
    }


def public_manual(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return None
    return {
        "id": doc.get("id"),
        "master_item_id": doc.get("master_item_id"),
        "accurate_item_id": doc.get("accurate_item_id"),
        "manual_current_qty": qty_num(doc.get("manual_current_qty")),
        "manual_purchase_qty": qty_num(doc.get("manual_purchase_qty")),
        "updated_at": doc.get("updated_at"),
        "updated_by": doc.get("updated_by"),
        "updated_by_name": doc.get("updated_by_name"),
    }
