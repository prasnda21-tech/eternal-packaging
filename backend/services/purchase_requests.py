"""Purchase Request (Permintaan) — source document for Pesanan Pembelian.

Exact line identity: line_idx + line_id. Same item name on two lines stays two lines.
Partial conversion to PO is supported; remaining_order_qty is backend-authoritative.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.purchase_common import (
    PREFIX_REQUEST,
    actor_snapshot,
    stamp_create,
    cancellation_fields,
    get_named_lock,
    insert_with_unique_number,
    is_cancelled,
    now_iso,
    parse_qty,
    remaining_qty,
    next_dated_seq,
    with_active_filter,
)

COLLECTION = "purchase_requests"
ORDERS_COLLECTION = "purchase_orders"


def _normalize_request_lines(raw_lines: Any) -> List[dict]:
    if not isinstance(raw_lines, list) or not raw_lines:
        raise HTTPException(400, "Minimal satu baris Permintaan wajib diisi.")
    lines = []
    for idx, raw in enumerate(raw_lines):
        if not isinstance(raw, dict):
            raise HTTPException(400, f"Baris Permintaan ke-{idx + 1} tidak valid.")
        master_item_id = str(raw.get("master_item_id") or "").strip()
        if not master_item_id:
            raise HTTPException(
                400,
                f"Baris ke-{idx + 1}: pilih item dari Master Item. Nama bebas tidak didukung.",
            )
        item_name = str(raw.get("item_name") or raw.get("name") or "").strip()
        if not item_name:
            raise HTTPException(400, f"Nama item baris ke-{idx + 1} wajib diisi.")
        qty = parse_qty(raw.get("requested_qty", raw.get("quantity")), f"Qty baris '{item_name}'")
        line_idx = idx
        if raw.get("line_idx") is not None and str(raw.get("line_idx")).strip() != "":
            try:
                line_idx = int(raw.get("line_idx"))
            except (TypeError, ValueError):
                line_idx = idx
        line_id = str(raw.get("line_id") or raw.get("request_line_id") or "").strip() or str(uuid.uuid4())
        lines.append({
            "line_idx": line_idx,
            "line_id": line_id,
            "request_line_id": line_id,
            "master_item_id": master_item_id,
            "accurate_item_id": str(raw.get("accurate_item_id") or "").strip(),
            "item_code": str(raw.get("item_code") or "").strip(),
            "item_name": item_name,
            "unit": str(raw.get("unit") or "pcs").strip() or "pcs",
            "requested_qty": qty,
            "notes": str(raw.get("notes") or "").strip(),
        })
    return lines


async def _ordered_qty_by_request_line(db, request_id: str) -> Dict[str, float]:
    pos = await db[ORDERS_COLLECTION].find(
        with_active_filter({"request_id": request_id}),
        {"_id": 0, "lines": 1},
    ).to_list(2000)
    out: Dict[str, float] = {}
    for po in pos:
        for li in po.get("lines") or []:
            key = str(li.get("request_line_id") or "")
            if not key:
                continue
            out[key] = out.get(key, 0.0) + float(li.get("ordered_qty") or 0)
    return out


def _with_order_progress(doc: dict, ordered_by_line: Dict[str, float], *, can_cancel: Optional[bool] = None) -> dict:
    out = dict(doc)
    lines = []
    total_requested = 0.0
    total_remaining = 0.0
    for li in out.get("lines") or []:
        row = dict(li)
        rid = str(row.get("line_id") or row.get("request_line_id") or "")
        requested = float(row.get("requested_qty") or 0)
        ordered = float(ordered_by_line.get(rid, 0.0))
        remain = remaining_qty(requested, ordered)
        row["cumulative_ordered_qty"] = ordered
        row["remaining_order_qty"] = remain
        total_requested += requested
        total_remaining += remain
        lines.append(row)
    out["lines"] = lines
    out["total_requested_qty"] = total_requested
    out["total_remaining_order_qty"] = total_remaining
    if is_cancelled(out):
        out["status"] = "DIBATALKAN"
    elif total_requested <= 0:
        out["status"] = "KOSONG"
    elif total_remaining <= 0:
        out["status"] = "SELESAI DIPESAN"
    elif total_remaining < total_requested:
        out["status"] = "SEBAGIAN DIPESAN"
    else:
        out["status"] = "TERBUKA"
    if can_cancel is not None:
        out["can_cancel"] = bool(can_cancel)
    return out


async def _active_po_count_for_request(db, request_id: str) -> int:
    return await db[ORDERS_COLLECTION].count_documents(with_active_filter({"request_id": str(request_id)}))


async def _enrich_request(db, doc: dict) -> dict:
    ordered = await _ordered_qty_by_request_line(db, doc["id"])
    cancelled = is_cancelled(doc)
    active_po = 0 if cancelled else await _active_po_count_for_request(db, doc["id"])
    can_cancel = (not cancelled) and active_po == 0
    out = _with_order_progress(doc, ordered, can_cancel=can_cancel)
    if cancelled:
        out["cancel_block_reason"] = "Permintaan sudah dibatalkan."
    elif active_po > 0:
        out["cancel_block_reason"] = "Permintaan tidak dapat dihapus karena sudah digunakan pada Pesanan Pembelian."
    else:
        out["cancel_block_reason"] = ""
    return out

async def create_purchase_request(db, payload: Dict[str, Any], user: dict) -> dict:
    async with get_named_lock("purchase_request"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return await _enrich_request(db, existing)

        lines = _normalize_request_lines(payload.get("lines") or payload.get("line_items"))
        ts = now_iso()
        doc = {
            "id": str(uuid.uuid4()),
            "request_date": str(payload.get("request_date") or ts[:10]).strip()[:10],
            "notes": str(payload.get("notes") or "").strip(),
            "requested_by": str(payload.get("requested_by") or "").strip(),
            "lines": lines,
            **stamp_create(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        async def _alloc():
            return await next_dated_seq(db, COLLECTION, "request_number", PREFIX_REQUEST)

        saved = await insert_with_unique_number(db, COLLECTION, "request_number", doc, _alloc)
        return await _enrich_request(db, saved)


async def list_purchase_requests(
    db,
    q: Optional[str] = None,
    eligible_for_po: bool = False,
    include_cancelled: bool = False,
) -> List[dict]:
    filters: Dict[str, Any] = {}
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"request_number": {"$regex": needle, "$options": "i"}},
            {"notes": {"$regex": needle, "$options": "i"}},
            {"lines.item_name": {"$regex": needle, "$options": "i"}},
        ]
    if not include_cancelled:
        filters = with_active_filter(filters)
    rows = await db[COLLECTION].find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)
    out = []
    for row in rows:
        enriched = await _enrich_request(db, row)
        if eligible_for_po and float(enriched.get("total_remaining_order_qty") or 0) <= 0:
            continue
        out.append(enriched)
    return out


async def get_purchase_request(db, request_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(request_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"request_number": str(request_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Permintaan '{request_id}' tidak ditemukan.")
    return await _enrich_request(db, doc)


async def cancel_purchase_request(db, request_id: str, user: dict, reason: str = "") -> dict:
    """Soft-cancel PR when no active PO references it. Idempotent."""
    reason = str(reason or "").strip()

    async with get_named_lock("purchase_request"):
        doc = await db[COLLECTION].find_one({"id": str(request_id)}, {"_id": 0})
        if not doc:
            doc = await db[COLLECTION].find_one({"request_number": str(request_id)}, {"_id": 0})
        if not doc:
            raise HTTPException(404, f"Permintaan '{request_id}' tidak ditemukan.")

        if is_cancelled(doc):
            return await _enrich_request(db, doc)

        active_po = await _active_po_count_for_request(db, doc["id"])
        if active_po > 0:
            raise HTTPException(
                409,
                "Permintaan tidak dapat dihapus karena sudah digunakan pada Pesanan Pembelian.",
            )

        fields = cancellation_fields(user, reason)
        await db[COLLECTION].update_one({"id": doc["id"]}, {"$set": fields})
        doc.update(fields)
        return await _enrich_request(db, doc)
