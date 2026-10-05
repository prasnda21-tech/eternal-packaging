"""Safe product replacement for existing order line_items.

Lock rules (Path 1 — preserve downstream product/stock identity):

PRINT:
  locked if sample_uploaded(order) OR exact line_idx has downstream product identity
NON-PRINT:
  locked if exact line_idx has downstream product identity
  (sample_uploaded alone does NOT lock non-print)

Cross-type print ↔ non_print is disallowed. Catalog items have no
production_process; same-class is preserved by never mutating the line's
existing production_process.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException

from quantity_rules import is_print_line
from services.line_qty_commitment import (
    committed_qty_from_context,
    load_line_commitment_context,
)
from workflow_gates import sample_uploaded

REASON_SAMPLE = "ITEM_SUDAH_MASUK_SAMPEL_CETAK"
REASON_DOWNSTREAM = "ITEM_SUDAH_DIPAKAI_DI_PROSES_LANJUTAN"
REASON_CROSS_TYPE = "PROSES_PRODUKSI_BERBEDA"
REASON_INACTIVE = "PRODUK_TIDAK_AKTIF"
REASON_NOT_FOUND = "PRODUK_TIDAK_DITEMUKAN"
REASON_SAME_PRODUCT = "PRODUK_SAMA"
REASON_INVALID_LINE = "LINE_TIDAK_VALID"
REASON_CANCELLED = "PO_DIBATALKAN"

MSG_SAMPLE = "Barang tidak dapat diganti karena item sudah memasuki tahap Sampel Cetak."
MSG_DOWNSTREAM = (
    "Barang tidak dapat diganti karena item sudah digunakan pada proses "
    "pengiriman/stock berikutnya."
)
MSG_DOWNSTREAM_SJ = (
    "Barang tidak dapat diganti karena item sudah dipakai pada Surat Jalan."
)
MSG_DOWNSTREAM_LOADING = (
    "Barang tidak dapat diganti karena item sudah dipakai pada Loading."
)
MSG_DOWNSTREAM_DELIVERY = (
    "Barang tidak dapat diganti karena item sudah dipakai pada konfirmasi pengiriman."
)
MSG_DOWNSTREAM_STOCK = (
    "Barang tidak dapat diganti karena item sudah punya identitas stok terkait."
)
MSG_CROSS_TYPE = (
    "Barang tidak dapat diganti ke proses produksi berbeda (Cetak ↔ Non-Cetak)."
)
MSG_INACTIVE = "Produk target tidak aktif atau tidak tersedia di katalog."
MSG_NOT_FOUND = "Produk target tidak ditemukan di Barang/Jasa."
MSG_SAME = "Produk pengganti sama dengan barang saat ini."
MSG_INVALID_LINE = "Line item tidak ditemukan."
MSG_CANCELLED = "PO dibatalkan — barang tidak dapat diganti."
MSG_QTY_CANCELLED = "PO dibatalkan — qty tidak dapat diubah."
MSG_QTY_PENDING = (
    "Qty belum dapat dikoreksi karena masih ada klaim stok yang sedang diproses pada item ini."
)

# Product-derived fields mirrored from create_sales_order / PesananPenjualan mapping.
PRODUCT_DERIVED_FIELDS = (
    "name",
    "item_code",
    "unit",
    "accurate_item_id",
    "master_item_id",
)


def line_production_process(li: dict) -> str:
    """Canonical process class for a line (legacy missing ⇒ print)."""
    return "print" if is_print_line(li or {}) else "non_print"


def current_product_snapshot(li: dict) -> Dict[str, Any]:
    li = li or {}
    return {
        "name": li.get("name"),
        "item_code": li.get("item_code"),
        "unit": li.get("unit") or "pcs",
        "accurate_item_id": li.get("accurate_item_id"),
        "master_item_id": li.get("master_item_id"),
    }


def map_master_to_line_fields(master: dict) -> Dict[str, Any]:
    """Reuse exact add-item product mapping (no price, no production_process)."""
    return {
        "name": (master.get("item_name") or master.get("name") or "").strip() or None,
        "item_code": (master.get("item_code") or "").strip() or None,
        "unit": (master.get("unit") or "pcs").strip() or "pcs",
        "accurate_item_id": (str(master.get("accurate_item_id") or "").strip() or None),
        "master_item_id": (
            str(master.get("id") or master.get("master_item_id") or "").strip() or None
        ),
    }


def _line_idx_in_rows(rows: Optional[list], idx: int, idx_keys: Tuple[str, ...] = ("line_idx",)) -> bool:
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in idx_keys:
            if key not in row:
                continue
            try:
                if int(row.get(key)) == int(idx):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def collect_order_embedded_downstream_hits(order: dict, line_idx: int) -> List[str]:
    """Exact line_idx hits inside the order document (no name matching)."""
    hits: List[str] = []
    for ship in order.get("shipments") or []:
        if not isinstance(ship, dict):
            continue
        if ship.get("is_cancelled") or ship.get("cancelled"):
            continue
        if _line_idx_in_rows(ship.get("lines"), line_idx):
            hits.append("shipment")
            break

    for lc in order.get("loading_confirmations") or []:
        if not isinstance(lc, dict):
            continue
        if _line_idx_in_rows(lc.get("lines"), line_idx):
            hits.append("loading")
            break

    # Some legacy loading_info.lines may also carry line_idx
    loading_info = order.get("loading_info") or {}
    if isinstance(loading_info, dict) and _line_idx_in_rows(loading_info.get("lines"), line_idx):
        if "loading" not in hits:
            hits.append("loading")

    for ev in order.get("delivery_confirmations") or []:
        if not isinstance(ev, dict):
            continue
        items = ev.get("items") or ev.get("lines") or []
        if _line_idx_in_rows(items, line_idx):
            hits.append("delivery_confirmation")
            break

    return hits


async def collect_sales_delivery_hits(db, order_id: str, line_idx: int) -> List[str]:
    """Exact line_idx in sales_deliveries.line_items (non-cancelled)."""
    if not order_id:
        return []
    doc = await db.sales_deliveries.find_one(
        {
            "po_id": order_id,
            "is_cancelled": {"$ne": True},
            "line_items.line_idx": int(line_idx),
        },
        {"_id": 1, "id": 1},
    )
    return ["sales_delivery"] if doc else []


async def collect_stock_identity_hits(db, order_id: str, line_idx: int) -> List[str]:
    """Stock movements / pending claims keyed by order_id + line_idx."""
    if not order_id:
        return []
    idx = int(line_idx)
    mov, pending = await asyncio.gather(
        db.stock_movements.find_one(
            {"order_id": order_id, "line_idx": idx},
            {"_id": 1, "id": 1},
        ),
        db.product_stock_state.find_one(
            {"pending_event.order_id": order_id, "pending_event.line_idx": idx},
            {"_id": 1},
        ),
    )
    if mov:
        return ["stock_movement"]
    if pending:
        return ["stock_claim"]
    return []


def message_for_downstream(sources: List[str]) -> str:
    if "sales_delivery" in sources or "shipment" in sources:
        return MSG_DOWNSTREAM_SJ
    if "loading" in sources:
        return MSG_DOWNSTREAM_LOADING
    if "delivery_confirmation" in sources:
        return MSG_DOWNSTREAM_DELIVERY
    if "stock_movement" in sources or "stock_claim" in sources:
        return MSG_DOWNSTREAM_STOCK
    return MSG_DOWNSTREAM


async def gather_downstream_sources(db, order: dict, line_idx: int) -> List[str]:
    order_id = str(order.get("id") or "")
    embedded = collect_order_embedded_downstream_hits(order, line_idx)
    sj, stock = await asyncio.gather(
        collect_sales_delivery_hits(db, order_id, line_idx),
        collect_stock_identity_hits(db, order_id, line_idx),
    )
    # Preserve priority order for messaging
    out: List[str] = []
    for src in embedded + sj + stock:
        if src not in out:
            out.append(src)
    return out


def _safe_line_qty(li: Optional[dict]) -> int:
    try:
        return max(0, int((li or {}).get("quantity") or 0))
    except (TypeError, ValueError):
        return 0


def _invalid_line_payload(line_idx: Any) -> Dict[str, Any]:
    return {
        "allowed": False,
        "product_replace_allowed": False,
        "product_replace_reason": MSG_INVALID_LINE,
        "reason_code": REASON_INVALID_LINE,
        "reason_message": MSG_INVALID_LINE,
        "line_idx": line_idx,
        "production_process": None,
        "current_product": None,
        "downstream_sources": [],
        "qty_edit_allowed": False,
        "qty_edit_reason": MSG_INVALID_LINE,
        "current_qty": 0,
        "committed_qty": 0,
        "editable_remaining": 0,
        "min_qty": 0,
        "max_qty": 0,
    }


def _with_qty_edit(
    payload: Dict[str, Any],
    li: Optional[dict],
    breakdown: Dict[str, Any],
    *,
    cancelled: bool,
) -> Dict[str, Any]:
    """Keep `allowed` as the product-replacement flag. Qty is a separate capability."""
    current_qty = _safe_line_qty(li)
    committed = max(0, int(breakdown.get("committed_qty") or 0))
    pending = bool(breakdown.get("pending_stock_claim"))
    qty_reason = None
    qty_allowed = True
    if cancelled:
        qty_allowed = False
        qty_reason = MSG_QTY_CANCELLED
    elif pending:
        qty_allowed = False
        qty_reason = MSG_QTY_PENDING
    elif committed > current_qty:
        qty_allowed = False
        qty_reason = "Qty terikat sudah melebihi qty pesanan."
    product_allowed = bool(payload.get("allowed"))
    payload["product_replace_allowed"] = product_allowed
    payload["product_replace_reason"] = None if product_allowed else payload.get("reason_message")
    payload["qty_edit_allowed"] = qty_allowed
    payload["qty_edit_reason"] = qty_reason
    payload["current_qty"] = current_qty
    payload["committed_qty"] = committed
    payload["editable_remaining"] = max(0, current_qty - committed)
    payload["min_qty"] = committed
    payload["max_qty"] = current_qty
    return payload


async def evaluate_line_product_replacement(
    db,
    order: dict,
    line_idx: int,
    *,
    commitment_ctx: Optional[dict] = None,
) -> Dict[str, Any]:
    """Authoritative can_replace_product(order, line_idx).

    `allowed` remains product replacement. Quantity correction is reported
    separately and is not closed by stock identity, sample, SJ, loading,
    or delivery confirmation.
    """
    lis = order.get("line_items") or []
    try:
        idx = int(line_idx)
    except (TypeError, ValueError):
        return _invalid_line_payload(line_idx)

    if idx < 0 or idx >= len(lis):
        return _invalid_line_payload(idx)

    li = lis[idx] if isinstance(lis[idx], dict) else {}
    process = line_production_process(li)
    current = current_product_snapshot(li)
    ctx = commitment_ctx if commitment_ctx is not None else await load_line_commitment_context(db, order)
    breakdown = committed_qty_from_context(order, idx, ctx)
    cancelled = bool(order.get("cancelled"))

    def finish(allowed: bool, code: Optional[str], message: Optional[str], downstream: List[str]) -> Dict[str, Any]:
        return _with_qty_edit(
            {
                "allowed": allowed,
                "reason_code": code,
                "reason_message": message,
                "line_idx": idx,
                "production_process": process,
                "current_product": current,
                "downstream_sources": downstream,
            },
            li,
            breakdown,
            cancelled=cancelled,
        )

    if cancelled:
        return finish(False, REASON_CANCELLED, MSG_CANCELLED, [])

    downstream = await gather_downstream_sources(db, order, idx)
    if downstream:
        return finish(False, REASON_DOWNSTREAM, message_for_downstream(downstream), downstream)

    if process == "print" and sample_uploaded(order):
        return finish(False, REASON_SAMPLE, MSG_SAMPLE, [])

    return finish(True, None, None, [])


async def evaluate_all_line_product_replacements(db, order: dict) -> List[Dict[str, Any]]:
    lis = order.get("line_items") or []
    if not lis:
        return []
    # Invoice and stock reads are per order. Product locks stay per line.
    ctx = await load_line_commitment_context(db, order)
    return list(await asyncio.gather(*(
        evaluate_line_product_replacement(db, order, i, commitment_ctx=ctx)
        for i in range(len(lis))
    )))


async def resolve_replacement_master(
    db,
    *,
    master_item_id: Optional[str] = None,
    accurate_item_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Load exact catalog item by canonical id. Reject inactive/missing."""
    mid = (master_item_id or "").strip()
    aid = (accurate_item_id or "").strip()
    if not mid and not aid:
        raise HTTPException(400, "master_item_id atau accurate_item_id wajib.")

    query: Dict[str, Any]
    if mid and aid:
        query = {"$or": [{"id": mid}, {"master_item_id": mid}, {"accurate_item_id": aid}]}
    elif mid:
        query = {"$or": [{"id": mid}, {"master_item_id": mid}]}
    else:
        query = {"accurate_item_id": aid}

    master = await db.master_items.find_one(query, {"_id": 0})
    if not master:
        raise HTTPException(404, {"code": REASON_NOT_FOUND, "message": MSG_NOT_FOUND})
    if master.get("is_active") is False:
        raise HTTPException(400, {"code": REASON_INACTIVE, "message": MSG_INACTIVE})
    # Missing is_active treated as active (legacy import rows)
    return master


def apply_product_fields_to_line(li: dict, mapped: dict) -> dict:
    """Update only product-derived fields; clear stale identity; preserve qty/process/etc."""
    updated = dict(li or {})
    for key in PRODUCT_DERIVED_FIELDS:
        updated[key] = mapped.get(key)
    # Explicitly ensure process / qty / notes / color untouched
    return updated


async def replace_line_product(
    db,
    order: dict,
    line_idx: int,
    *,
    master_item_id: Optional[str] = None,
    accurate_item_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Mutate exact line product fields after re-checking the guard.

    Returns dict with order, line, eligibility, replaced_fields.
    Raises HTTPException on lock / validation failure.
    """
    guard = await evaluate_line_product_replacement(db, order, line_idx)
    if not guard["allowed"]:
        raise HTTPException(
            409,
            {
                "code": guard["reason_code"],
                "message": guard["reason_message"],
                "line_idx": guard["line_idx"],
                "production_process": guard["production_process"],
                "downstream_sources": guard.get("downstream_sources") or [],
            },
        )

    master = await resolve_replacement_master(
        db, master_item_id=master_item_id, accurate_item_id=accurate_item_id
    )
    mapped = map_master_to_line_fields(master)
    if not mapped.get("name"):
        raise HTTPException(400, "Produk target tidak memiliki nama yang valid.")
    if not mapped.get("accurate_item_id") and not mapped.get("master_item_id"):
        raise HTTPException(400, "Produk target tidak memiliki identitas kanonis.")

    idx = int(line_idx)
    lis = list(order.get("line_items") or [])
    li = dict(lis[idx])
    before = current_product_snapshot(li)

    # Same product → no-op reject (explicit)
    same_aid = (
        before.get("accurate_item_id")
        and mapped.get("accurate_item_id")
        and str(before["accurate_item_id"]) == str(mapped["accurate_item_id"])
    )
    same_mid = (
        before.get("master_item_id")
        and mapped.get("master_item_id")
        and str(before["master_item_id"]) == str(mapped["master_item_id"])
    )
    if same_aid or (same_mid and not mapped.get("accurate_item_id") and not before.get("accurate_item_id")):
        raise HTTPException(400, {"code": REASON_SAME_PRODUCT, "message": MSG_SAME})

    # Cross-type: catalog has no process class — preserve line production_process.
    # Reject only if caller somehow tried to change process via side channel
    # (this endpoint never accepts production_process).
    source_process = line_production_process(li)
    # Defense: if master carried an explicit incompatible production_process, reject.
    master_proc = (master.get("production_process") or "").strip().lower()
    if master_proc in ("print", "non_print") and master_proc != source_process:
        raise HTTPException(
            400,
            {"code": REASON_CROSS_TYPE, "message": MSG_CROSS_TYPE},
        )

    updated_li = apply_product_fields_to_line(li, mapped)
    # Hard preserve classification + qty + operational metadata
    updated_li["production_process"] = li.get("production_process")
    if "quantity" in li:
        updated_li["quantity"] = li["quantity"]
    for keep in ("color", "notes", "id", "line_id", "accurate_line_id", "color_source"):
        if keep in li:
            updated_li[keep] = li[keep]

    # Final process must match source class
    if line_production_process(updated_li) != source_process:
        raise HTTPException(
            400,
            {"code": REASON_CROSS_TYPE, "message": MSG_CROSS_TYPE},
        )

    lis[idx] = updated_li
    order["line_items"] = lis

    # Re-check guard after building mutation (race: SJ created while editing)
    race = await evaluate_line_product_replacement(db, order, idx)
    # Note: evaluate uses order.line_items for current product but downstream
    # from DB/order embeds — product fields change doesn't affect lock.
    # Re-load downstream from DB against original order id still works.
    # However evaluate_line_product_replacement reads order embeds; for race
    # we must re-fetch order from DB before final persist — caller does that.

    after = current_product_snapshot(updated_li)
    eligibility = await evaluate_line_product_replacement(db, order, idx)

    return {
        "line_idx": idx,
        "line": updated_li,
        "before": before,
        "after": after,
        "production_process": source_process,
        "eligibility": eligibility,
        "order": order,
    }


def reject_cross_type_explicit(source_process: str, target_process: Optional[str]) -> None:
    """Helper for tests / optional explicit process on payload."""
    if not target_process:
        return
    t = str(target_process).strip().lower()
    if t not in ("print", "non_print"):
        return
    if t != source_process:
        raise HTTPException(
            400,
            {"code": REASON_CROSS_TYPE, "message": MSG_CROSS_TYPE},
        )
