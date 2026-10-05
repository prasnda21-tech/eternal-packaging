"""Committed quantity floor for an existing sales-order line.

COMMITTED_QTY is the max of real commitments, not the sum of pipeline stages.
The same physical goods can appear as operator output, packing, SJ, and invoice.

Pending stock claims are not added into the numeric floor. While a pending
claim exists for the line, quantity correction is rejected outright. That is
safer than guessing an in-flight increment that is not yet on the order, and
it closes the window where a claim is visible before operator progress commits.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from quantity_rules import (
    is_print_line,
    qty_confirmed_loading_for_line,
    qty_packed_for_line,
    qty_shipped_for_line,
)
from services.purchase_common import line_subtotal, money_round
from services.sales_invoices import FINANCIAL_CANCELLED, effective_financial_status
from services.workflow_entries import sum_entry_qty

REASON_QTY_BELOW = "QTY_DI_BAWAH_QTY_TERIKAT"
REASON_QTY_PENDING = "QTY_KOREKSI_DITUNDA_KLAIM_STOK"
REASON_QTY_DELIVERED = "QTY_TIDAK_BISA_DITAMBAH_PESANAN_TERKIRIM_LENGKAP"
REASON_SCHEDULE = "JADWAL_OPERATOR_PERLU_DISESUAIKAN"

MSG_PENDING = "Stok sedang diproses Operator. Perubahan qty sementara dikunci."
MSG_QTY_DELIVERED = "Qty tidak dapat ditambah karena pesanan sudah terkirim lengkap."
MSG_SCHEDULE = (
    "Qty pesanan tidak dapat diturunkan karena sisa jadwal operator terbagi ke lebih dari satu operator. "
    "Sesuaikan jadwal operator terlebih dahulu."
)

_CETAK_TYPES = frozenset({"STATUS_CETAK", "PEMAKAIAN_CETAK"})
_PENGIRIMAN_TYPES = frozenset({"PENGIRIMAN"})
_STOCK_TYPES = _CETAK_TYPES | _PENGIRIMAN_TYPES


class QuantityUnchanged(Exception):
    """Qty-only request equals the current line quantity. Do not persist."""


def format_qty_id(value: int) -> str:
    return f"{int(value):,}".replace(",", ".")


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default


def qty_reject_detail(
    code: str,
    message: str,
    *,
    current_qty: int,
    committed_qty: int,
    requested_qty: int,
) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "current_qty": int(current_qty),
        "committed_qty": int(committed_qty),
        "minimum_qty": int(committed_qty),
        "requested_qty": int(requested_qty),
    }


def message_qty_below(current_qty: int, committed_qty: int, requested_qty: int) -> str:
    return (
        f"Pesanan: {format_qty_id(current_qty)}. "
        f"Sudah terikat: {format_qty_id(committed_qty)}. "
        f"Qty minimum: {format_qty_id(committed_qty)}. "
        f"Qty diminta: {format_qty_id(requested_qty)}."
    )


def split_operator_completed(split: Optional[dict]) -> int:
    """Completed operator qty for one split.

    Valid progress entries win when they exist, so three confirmed 1.000
    entries stay 3.000 even if the snapshot field is stale. Cancelled and
    replaced entries are ignored by sum_entry_qty.
    """
    split = split or {}
    entries = split.get("progress_entries")
    if isinstance(entries, list) and entries:
        return max(0, sum_entry_qty(entries))
    return max(0, _safe_int(split.get("qty_completed")))


def _split_line_idx(split: dict) -> Optional[int]:
    raw = split.get("line_idx")
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _print_line_indexes(order: dict) -> List[int]:
    out = []
    for i, li in enumerate(order.get("line_items") or []):
        if isinstance(li, dict) and is_print_line(li):
            out.append(i)
    return out


def splits_for_line(order: dict, line_idx: int) -> Tuple[List[dict], List[dict]]:
    """Return (line-scoped splits, unscoped splits).

    A split without line_idx is attributed only when the order has exactly
    one print line. Otherwise it is left unscoped and is never edited here.
    """
    idx = int(line_idx)
    matched: List[dict] = []
    unscoped: List[dict] = []
    single_print = _print_line_indexes(order) == [idx]
    for split in order.get("operator_splits") or []:
        if not isinstance(split, dict):
            continue
        bound = _split_line_idx(split)
        if bound is None:
            unscoped.append(split)
            continue
        if bound == idx:
            matched.append(split)
    if single_print:
        matched.extend(unscoped)
        unscoped = []
    return matched, unscoped


def operator_completed_qty_for_line(order: dict, line_idx: int) -> int:
    matched, _unscoped = splits_for_line(order, line_idx)
    return sum(split_operator_completed(split) for split in matched)


def operator_progress_line_cap_error(
    order: dict,
    splits: List[dict],
    found: dict,
    new_total: int,
) -> Optional[str]:
    """Reject operator completed total above the latest qty of that line.

    The split schedule cap is not enough: a stale allocation can stay above
    the order line after a quantity correction, and an order-level print total
    can still allow one line to exceed its own quantity when other lines remain.
    """
    idx = _split_line_idx(found or {})
    if idx is None:
        return None
    lis = order.get("line_items") or []
    if idx < 0 or idx >= len(lis) or not isinstance(lis[idx], dict):
        return "Line item operator tidak valid."
    line_qty = max(0, _safe_int(lis[idx].get("quantity")))
    others = 0
    found_id = (found or {}).get("id")
    for split in splits or []:
        if not isinstance(split, dict) or split.get("id") == found_id:
            continue
        if _split_line_idx(split) != idx:
            continue
        others += split_operator_completed(split)
    total = others + max(0, int(new_total))
    if total > line_qty:
        return (
            f"Qty selesai operator ({format_qty_id(total)}) melebihi "
            f"qty pesanan terkini ({format_qty_id(line_qty)})."
        )
    return None


def _add_qty(bucket: Dict[int, int], line_idx: Any, qty: int) -> None:
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        return
    bucket[idx] = bucket.get(idx, 0) + max(0, int(qty))


def _movement_counts(doc: dict) -> bool:
    if not isinstance(doc, dict):
        return False
    if doc.get("voided_at") or doc.get("voided") is True:
        return False
    return str(doc.get("movement_type") or "").strip().upper() in _STOCK_TYPES


async def load_line_commitment_context(db, order: dict) -> Dict[str, Any]:
    """One read of invoice, stock movement, and pending claim for the order."""
    ctx: Dict[str, Any] = {
        "invoiced": {},
        "stock_cetak": {},
        "stock_pengiriman": {},
        "pending_lines": set(),
    }
    order_id = str((order or {}).get("id") or "")
    if not order_id or db is None:
        return ctx
    movements, invoices, pendings = await asyncio.gather(
        db.stock_movements.find(
            {"order_id": order_id, "movement_type": {"$in": sorted(_STOCK_TYPES)}},
            {"_id": 0, "line_idx": 1, "movement_type": 1, "transaction_qty": 1, "voided_at": 1, "voided": 1},
        ).to_list(5000),
        db.sales_invoices.find(
            {"po_id": order_id},
            {"_id": 0, "lines": 1, "financial_status": 1},
        ).to_list(500),
        db.product_stock_state.find(
            {"pending_event.order_id": order_id},
            {"_id": 0, "pending_event.order_id": 1, "pending_event.line_idx": 1},
        ).to_list(200),
    )
    for doc in movements:
        if not _movement_counts(doc):
            continue
        kind = str(doc.get("movement_type") or "").strip().upper()
        bucket = ctx["stock_cetak"] if kind in _CETAK_TYPES else ctx["stock_pengiriman"]
        _add_qty(bucket, doc.get("line_idx"), _safe_int(doc.get("transaction_qty")))
    for inv in invoices:
        if effective_financial_status(inv) == FINANCIAL_CANCELLED:
            continue
        for line in inv.get("lines") or []:
            if not isinstance(line, dict):
                continue
            _add_qty(ctx["invoiced"], line.get("line_idx", 0), _safe_int(line.get("quantity")))
    for state in pendings:
        pending = (state or {}).get("pending_event") or {}
        if str(pending.get("order_id") or "") != order_id:
            continue
        try:
            ctx["pending_lines"].add(int(pending.get("line_idx")))
        except (TypeError, ValueError):
            continue
    return ctx


def committed_qty_from_context(order: dict, line_idx: int, ctx: Optional[dict]) -> Dict[str, Any]:
    idx = int(line_idx)
    ctx = ctx or {}
    operator_qty = operator_completed_qty_for_line(order, idx)
    packed_qty = max(0, qty_packed_for_line(order, idx))
    loading_qty = max(0, qty_confirmed_loading_for_line(order, idx))
    shipped_qty = max(0, qty_shipped_for_line(order, idx))
    invoiced_qty = max(0, _safe_int((ctx.get("invoiced") or {}).get(idx)))
    cetak = max(0, _safe_int((ctx.get("stock_cetak") or {}).get(idx)))
    pengiriman = max(0, _safe_int((ctx.get("stock_pengiriman") or {}).get(idx)))
    stock_qty = max(cetak, pengiriman)
    committed = max(operator_qty, packed_qty, loading_qty, shipped_qty, invoiced_qty, stock_qty)
    return {
        "committed_qty": committed,
        "operator_qty": operator_qty,
        "packed_qty": packed_qty,
        "loading_qty": loading_qty,
        "shipped_qty": shipped_qty,
        "invoiced_qty": invoiced_qty,
        "stock_qty": stock_qty,
        "pending_stock_claim": idx in (ctx.get("pending_lines") or set()),
    }


async def get_committed_qty_for_line(db, order: dict, line_idx: int, *, ctx: Optional[dict] = None) -> Dict[str, Any]:
    loaded = ctx if ctx is not None else await load_line_commitment_context(db, order)
    return committed_qty_from_context(order, line_idx, loaded)


def _uncompleted_remainder(split: dict) -> int:
    scheduled = max(0, _safe_int(split.get("quantity")))
    completed = split_operator_completed(split)
    return max(0, scheduled - completed)


def reconcile_operator_schedule_for_line(order: dict, line_idx: int, new_qty: int) -> Dict[str, Any]:
    """Trim one uncompleted remainder so future operator work cannot pass new_qty.

    Completed quantities and progress entries stay. If more than one split still
    has uncompleted allocation, the reduction is rejected instead of guessing
    which operator should lose the remainder.
    """
    new_qty = int(new_qty)
    matched, unscoped = splits_for_line(order, line_idx)
    allocated = sum(max(0, _safe_int(split.get("quantity"))) for split in matched)
    if allocated <= new_qty:
        return {"trimmed": False, "allocated_qty": allocated}

    excess = allocated - new_qty
    if unscoped and any(
        _uncompleted_remainder(split) > 0 or split_operator_completed(split) > 0
        for split in unscoped
    ):
        raise HTTPException(409, {
            "code": REASON_SCHEDULE,
            "message": MSG_SCHEDULE,
        })

    flexible = [split for split in matched if _uncompleted_remainder(split) > 0]
    if len(flexible) != 1:
        raise HTTPException(409, {
            "code": REASON_SCHEDULE,
            "message": MSG_SCHEDULE,
        })
    split = flexible[0]
    completed = split_operator_completed(split)
    scheduled = max(0, _safe_int(split.get("quantity")))
    if excess > _uncompleted_remainder(split):
        raise HTTPException(409, {
            "code": REASON_SCHEDULE,
            "message": MSG_SCHEDULE,
        })
    reduced = scheduled - excess
    if reduced < completed:
        raise HTTPException(409, {
            "code": REASON_SCHEDULE,
            "message": MSG_SCHEDULE,
        })
    split["quantity"] = reduced
    split["qty_cancelled"] = max(0, _safe_int(split.get("qty_cancelled"))) + excess
    return {
        "trimmed": True,
        "allocated_qty": allocated - excess,
        "split_id": split.get("id"),
        "completed_qty": completed,
    }


def _price_missing(raw: Any) -> bool:
    return raw is None or (isinstance(raw, str) and not raw.strip())


def recalc_derived_selling_amounts(order: dict) -> None:
    """Refresh line_subtotal and selling_total from the stored unit_price.

    Does not read the current price master and does not change unit_price.
    """
    known: List[Decimal] = []
    for li in order.get("line_items") or []:
        if not isinstance(li, dict):
            continue
        raw_price = li.get("unit_price")
        if _price_missing(raw_price):
            existing = li.get("line_subtotal")
            if existing is not None and str(existing).strip() != "":
                known.append(Decimal(str(existing)))
            continue
        subtotal = line_subtotal(li.get("quantity"), raw_price)
        li["line_subtotal"] = subtotal
        known.append(Decimal(str(subtotal)))
    if known:
        order["selling_total"] = money_round(sum(known))
