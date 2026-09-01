"""Purchase Order (Pesanan Pembelian).

HPP unit cost source is always this document's exact line unit_price.
Physical on-hand stock is never changed by creating a PO.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.purchase_common import (
    PREFIX_PURCHASE_ORDER,
    actor_snapshot,
    get_named_lock,
    insert_with_unique_number,
    item_identity,
    line_subtotal,
    money_round,
    now_iso,
    parse_money,
    parse_qty,
    qty_le,
    remaining_qty,
    next_dated_seq,
)
from services.purchase_requests import get_purchase_request

COLLECTION = "purchase_orders"
GR_COLLECTION = "purchase_goods_receipts"
INV_COLLECTION = "purchase_invoices"
ALLOC_COLLECTION = "purchase_receipt_po_links"


async def _received_qty_by_po_line(db, purchase_order_id: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    receipts = await db[GR_COLLECTION].find(
        {"purchase_order_id": purchase_order_id}, {"_id": 0, "lines": 1}
    ).to_list(2000)
    for rec in receipts:
        for li in rec.get("lines") or []:
            key = str(li.get("purchase_order_line_id") or "")
            if not key:
                continue
            out[key] = out.get(key, 0.0) + float(li.get("received_qty") or 0)
    allocs = await db[ALLOC_COLLECTION].find(
        {"purchase_order_id": purchase_order_id}, {"_id": 0, "purchase_order_line_id": 1, "allocated_qty": 1}
    ).to_list(5000)
    for a in allocs:
        key = str(a.get("purchase_order_line_id") or "")
        if not key:
            continue
        out[key] = out.get(key, 0.0) + float(a.get("allocated_qty") or 0)
    return out


async def _invoiced_qty_by_po_line(db, purchase_order_id: str, exclude_invoice_id: Optional[str] = None) -> Dict[str, float]:
    q: Dict[str, Any] = {"purchase_order_id": purchase_order_id}
    if exclude_invoice_id:
        q["id"] = {"$ne": str(exclude_invoice_id)}
    invoices = await db[INV_COLLECTION].find(q, {"_id": 0, "lines": 1}).to_list(2000)
    out: Dict[str, float] = {}
    for inv in invoices:
        for li in inv.get("lines") or []:
            key = str(li.get("purchase_order_line_id") or "")
            if not key:
                continue
            out[key] = out.get(key, 0.0) + float(li.get("invoice_qty") or li.get("quantity") or 0)
    return out


def _with_progress(doc: dict, received: Dict[str, float], invoiced: Dict[str, float]) -> dict:
    out = dict(doc)
    lines = []
    rec_total = 0.0
    inv_total = 0.0
    ord_total = 0.0
    for li in out.get("lines") or []:
        row = dict(li)
        lid = str(row.get("line_id") or "")
        ordered = float(row.get("ordered_qty") or 0)
        rec = float(received.get(lid, 0.0))
        inv = float(invoiced.get(lid, 0.0))
        row["cumulative_received_qty"] = rec
        row["remaining_receivable_qty"] = remaining_qty(ordered, rec)
        row["cumulative_invoiced_qty"] = inv
        row["remaining_invoice_qty"] = remaining_qty(ordered, inv)
        row["item_identity"] = item_identity({**row, "purchase_order_id": out.get("id")})
        rec_total += rec
        inv_total += inv
        ord_total += ordered
        lines.append(row)
    out["lines"] = lines
    out["receiving_progress"] = {
        "ordered_qty": ord_total,
        "received_qty": rec_total,
        "remaining_qty": remaining_qty(ord_total, rec_total),
    }
    out["invoice_progress"] = {
        "ordered_qty": ord_total,
        "invoiced_qty": inv_total,
        "remaining_qty": remaining_qty(ord_total, inv_total),
    }
    if rec_total <= 0:
        recv_status = "BELUM DITERIMA"
    elif rec_total < ord_total:
        recv_status = "SEBAGIAN DITERIMA"
    else:
        recv_status = "DITERIMA"
    if inv_total <= 0:
        inv_status = "BELUM DITAGIH"
    elif inv_total < ord_total:
        inv_status = "SEBAGIAN DITAGIH"
    else:
        inv_status = "DITAGIH"
    out["receiving_status"] = recv_status
    out["invoice_status"] = inv_status
    out["status"] = recv_status
    return out


async def enrich_purchase_order(db, doc: dict) -> dict:
    received = await _received_qty_by_po_line(db, doc["id"])
    invoiced = await _invoiced_qty_by_po_line(db, doc["id"])
    return _with_progress(doc, received, invoiced)


async def get_request_po_preview(db, request_id: str) -> dict:
    req = await get_purchase_request(db, request_id)
    lines = []
    for li in req.get("lines") or []:
        remain = float(li.get("remaining_order_qty") or 0)
        lines.append({
            "line_idx": li.get("line_idx"),
            "line_id": li.get("line_id"),
            "request_line_id": li.get("line_id"),
            "request_line_idx": li.get("line_idx"),
            "master_item_id": li.get("master_item_id") or "",
            "accurate_item_id": li.get("accurate_item_id") or "",
            "item_code": li.get("item_code") or "",
            "item_name": li.get("item_name") or "",
            "unit": li.get("unit") or "pcs",
            "requested_qty": li.get("requested_qty") or 0,
            "cumulative_ordered_qty": li.get("cumulative_ordered_qty") or 0,
            "remaining_order_qty": remain,
            "ordered_qty": remain,
            "unit_price": 0.0,
            "line_subtotal": 0.0,
        })
    return {
        "request_id": req["id"],
        "request_number": req.get("request_number"),
        "request_date": req.get("request_date"),
        "notes": req.get("notes") or "",
        "lines": lines,
        "total_remaining_order_qty": req.get("total_remaining_order_qty") or 0,
    }


async def create_purchase_order(db, payload: Dict[str, Any], user: dict) -> dict:
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        raise HTTPException(400, "ID Permintaan (request_id) wajib diisi.")
    supplier = str(payload.get("supplier") or payload.get("supplier_name") or "").strip()
    if not supplier:
        raise HTTPException(400, "Supplier wajib diisi.")

    async with get_named_lock("purchase_order"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return await enrich_purchase_order(db, existing)

        preview = await get_request_po_preview(db, request_id)
        preview_by_id = {str(l["request_line_id"]): l for l in preview["lines"]}
        input_lines = payload.get("lines") or payload.get("line_items") or []
        calculated = []
        total = 0.0

        if input_lines:
            seen = set()
            for inp in input_lines:
                req_line_id = str(inp.get("request_line_id") or inp.get("line_id") or "").strip()
                src = preview_by_id.get(req_line_id)
                if src is None:
                    raise HTTPException(400, f"Baris Permintaan '{req_line_id}' tidak ditemukan pada permintaan ini.")
                if req_line_id in seen:
                    raise HTTPException(400, "Baris Permintaan tidak boleh digabung atau diulang pada satu PO.")
                seen.add(req_line_id)
                raw_qty = inp.get("ordered_qty", inp.get("quantity"))
                qty = parse_qty(raw_qty if raw_qty not in (None, "") else src["remaining_order_qty"], f"Qty '{src['item_name']}'")
                remain = float(src["remaining_order_qty"] or 0)
                if not qty_le(qty, remain):
                    raise HTTPException(
                        400,
                        f"Qty pesanan ({qty}) untuk '{src['item_name']}' melebihi sisa Permintaan ({remain}).",
                    )
                unit_price = parse_money(inp.get("unit_price"), f"Harga satuan '{src['item_name']}'")
                sub = line_subtotal(qty, unit_price)
                total += sub
                calculated.append({
                    "line_idx": src["line_idx"],
                    "line_id": str(uuid.uuid4()),
                    "request_line_id": src["request_line_id"],
                    "request_line_idx": src["request_line_idx"],
                    "master_item_id": src["master_item_id"],
                    "accurate_item_id": src["accurate_item_id"],
                    "item_code": src["item_code"],
                    "item_name": src["item_name"],
                    "unit": src["unit"],
                    "ordered_qty": qty,
                    "unit_price": unit_price,
                    "line_subtotal": sub,
                })
        else:
            for src in preview["lines"]:
                qty = float(src["remaining_order_qty"] or 0)
                if qty <= 0:
                    continue
                unit_price = 0.0
                calculated.append({
                    "line_idx": src["line_idx"],
                    "line_id": str(uuid.uuid4()),
                    "request_line_id": src["request_line_id"],
                    "request_line_idx": src["request_line_idx"],
                    "master_item_id": src["master_item_id"],
                    "accurate_item_id": src["accurate_item_id"],
                    "item_code": src["item_code"],
                    "item_name": src["item_name"],
                    "unit": src["unit"],
                    "ordered_qty": qty,
                    "unit_price": unit_price,
                    "line_subtotal": 0.0,
                })

        if not calculated:
            raise HTTPException(400, "Tidak ada kuantitas tersisa untuk dipesan dari Permintaan ini.")

        ts = now_iso()
        total = money_round(total)
        doc = {
            "id": str(uuid.uuid4()),
            "request_id": preview["request_id"],
            "request_number": preview["request_number"],
            "supplier": supplier,
            "order_date": str(payload.get("order_date") or ts[:10]).strip()[:10],
            "notes": str(payload.get("notes") or "").strip(),
            "lines": calculated,
            "subtotal": total,
            "grand_total": total,
            "created_at": ts,
            "updated_at": ts,
            "created_by": actor_snapshot(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        async def _alloc():
            return await next_dated_seq(db, COLLECTION, "po_number", PREFIX_PURCHASE_ORDER)

        saved = await insert_with_unique_number(db, COLLECTION, "po_number", doc, _alloc)
        return await enrich_purchase_order(db, saved)


async def list_purchase_orders(
    db,
    q: Optional[str] = None,
    request_id: Optional[str] = None,
    eligible_for_invoice: bool = False,
    eligible_for_receipt: bool = False,
) -> List[dict]:
    filters: Dict[str, Any] = {}
    if request_id:
        filters["request_id"] = str(request_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"request_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
            {"lines.item_name": {"$regex": needle, "$options": "i"}},
        ]
    rows = await db[COLLECTION].find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)
    out = []
    for row in rows:
        enriched = await enrich_purchase_order(db, row)
        if eligible_for_invoice and float(enriched.get("invoice_progress", {}).get("remaining_qty") or 0) <= 0:
            continue
        if eligible_for_receipt and float(enriched.get("receiving_progress", {}).get("remaining_qty") or 0) <= 0:
            continue
        out.append(enriched)
    return out


async def get_purchase_order(db, po_id: str) -> dict:
    doc = await db[COLLECTION].find_one({"id": str(po_id)}, {"_id": 0})
    if not doc:
        doc = await db[COLLECTION].find_one({"po_number": str(po_id)}, {"_id": 0})
    if not doc:
        raise HTTPException(404, f"Pesanan Pembelian '{po_id}' tidak ditemukan.")
    return await enrich_purchase_order(db, doc)
