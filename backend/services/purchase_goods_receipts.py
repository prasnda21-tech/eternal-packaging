"""PO-linked goods receipts shown on existing Penerimaan Barang page.

Does NOT write Accurate receipt documents.
Does NOT change physical Stock Saat Ini (Accurate aggregate remains source of truth).
Over-receipt vs exact PO line is rejected.
Accurate receipt lines may be allocated to exact PO lines via overlay collection.
"""
from __future__ import annotations

import uuid
from typing import Any, Dict, List, Optional

from fastapi import HTTPException

from services.purchase_common import (
    PREFIX_GOODS_RECEIPT,
    actor_snapshot,
    derive_child_number,
    get_named_lock,
    insert_with_unique_number,
    now_iso,
    parse_qty,
    qty_le,
    remaining_qty,
)
from services.purchase_orders import get_purchase_order
from services.physical_stock_engine import (
    handle_goods_receipt_created,
    precheck_goods_receipt_lines,
)

COLLECTION = "purchase_goods_receipts"
ALLOC_COLLECTION = "purchase_receipt_po_links"


async def get_po_receipt_preview(db, purchase_order_id: str) -> dict:
    po = await get_purchase_order(db, purchase_order_id)
    lines = []
    for li in po.get("lines") or []:
        remain = float(li.get("remaining_receivable_qty") or 0)
        lines.append({
            "purchase_order_line_id": li.get("line_id"),
            "line_idx": li.get("line_idx"),
            "line_id": li.get("line_id"),
            "request_line_id": li.get("request_line_id"),
            "master_item_id": li.get("master_item_id") or "",
            "accurate_item_id": li.get("accurate_item_id") or "",
            "item_code": li.get("item_code") or "",
            "item_name": li.get("item_name") or "",
            "unit": li.get("unit") or "pcs",
            "ordered_qty": li.get("ordered_qty") or 0,
            "cumulative_received_qty": li.get("cumulative_received_qty") or 0,
            "remaining_receivable_qty": remain,
            "received_qty": remain,
            "unit_price": li.get("unit_price") or 0,
        })
    return {
        "purchase_order_id": po["id"],
        "po_number": po.get("po_number"),
        "request_id": po.get("request_id"),
        "request_number": po.get("request_number"),
        "supplier": po.get("supplier"),
        "order_date": po.get("order_date"),
        "lines": lines,
        "receiving_progress": po.get("receiving_progress") or {},
    }


async def create_purchase_goods_receipt(db, payload: Dict[str, Any], user: dict) -> dict:
    po_id = str(payload.get("purchase_order_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Pembelian (purchase_order_id) wajib diisi.")

    async with get_named_lock("purchase_goods_receipt"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                await handle_goods_receipt_created(db, existing, user)
                return existing

        preview = await get_po_receipt_preview(db, po_id)
        by_line = {str(l["purchase_order_line_id"]): l for l in preview["lines"]}
        input_lines = payload.get("lines") or payload.get("line_items") or []
        if not input_lines:
            raise HTTPException(400, "Minimal satu baris penerimaan wajib diisi.")

        calculated = []
        seen = set()
        for inp in input_lines:
            po_line_id = str(inp.get("purchase_order_line_id") or inp.get("line_id") or "").strip()
            src = by_line.get(po_line_id)
            if src is None:
                raise HTTPException(400, f"Baris PO '{po_line_id}' tidak ditemukan.")
            if po_line_id in seen:
                raise HTTPException(400, "Baris PO tidak boleh digabung pada satu penerimaan.")
            seen.add(po_line_id)
            raw_qty = inp.get("received_qty", inp.get("quantity"))
            if raw_qty in (None, ""):
                continue
            qty = parse_qty(raw_qty, f"Qty diterima '{src['item_name']}'")
            remain = float(src["remaining_receivable_qty"] or 0)
            if not qty_le(qty, remain):
                raise HTTPException(
                    400,
                    f"Qty diterima ({qty}) untuk '{src['item_name']}' melebihi sisa PO ({remain}).",
                )
            calculated.append({
                "line_idx": src["line_idx"],
                "line_id": str(uuid.uuid4()),
                "purchase_order_line_id": src["purchase_order_line_id"],
                "request_line_id": src.get("request_line_id") or "",
                "master_item_id": src["master_item_id"],
                "accurate_item_id": src["accurate_item_id"],
                "item_code": src["item_code"],
                "item_name": src["item_name"],
                "unit": src["unit"],
                "received_qty": qty,
                "unit_price": src["unit_price"],
            })

        if not calculated:
            raise HTTPException(400, "Total kuantitas diterima harus lebih besar dari 0.")

        ts = now_iso()
        existing_count = await db[COLLECTION].count_documents({"purchase_order_id": preview["purchase_order_id"]})

        doc = {
            "id": str(uuid.uuid4()),
            "purchase_order_id": preview["purchase_order_id"],
            "po_number": preview["po_number"],
            "request_id": preview.get("request_id"),
            "request_number": preview.get("request_number"),
            "supplier": preview.get("supplier"),
            "receipt_date": str(payload.get("receipt_date") or ts[:10]).strip()[:10],
            "notes": str(payload.get("notes") or "").strip(),
            "source": "po_linked",
            "lines": calculated,
            "created_at": ts,
            "updated_at": ts,
            "created_by": actor_snapshot(user),
        }
        if idem:
            doc["idempotency_key"] = idem

        po_doc = await db.purchase_orders.find_one({"id": preview["purchase_order_id"]}, {"_id": 0})
        po_snapshot = (po_doc or {}).get("physical_stock_snapshot")
        doc["physical_stock_snapshot"] = po_snapshot
        await precheck_goods_receipt_lines(db, doc["lines"], po_snapshot)

        async def _alloc():
            idx = existing_count + 1
            candidate = derive_child_number(preview["po_number"], PREFIX_GOODS_RECEIPT, idx)
            while await db[COLLECTION].find_one({"receipt_number": candidate}, {"_id": 1}):
                idx += 1
                candidate = derive_child_number(preview["po_number"], PREFIX_GOODS_RECEIPT, idx)
            return candidate

        saved = await insert_with_unique_number(db, COLLECTION, "receipt_number", doc, _alloc)
        await handle_goods_receipt_created(db, saved, user)
        return saved


async def list_purchase_goods_receipts(
    db,
    q: Optional[str] = None,
    purchase_order_id: Optional[str] = None,
) -> List[dict]:
    filters: Dict[str, Any] = {}
    if purchase_order_id:
        filters["purchase_order_id"] = str(purchase_order_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"receipt_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"supplier": {"$regex": needle, "$options": "i"}},
        ]
    return await db[COLLECTION].find(filters, {"_id": 0}).sort("created_at", -1).to_list(1000)


async def allocate_accurate_receipt_line(db, payload: Dict[str, Any], user: dict) -> dict:
    """Overlay only — Accurate receipt documents are not mutated.

    Integrity rules:
    - allocated_qty > 0 (via parse_qty)
    - allocated_qty <= remaining PO receivable qty (GR lokal + prior allocations)
    - allocated_qty <= remaining Accurate line qty (received - prior allocations of same line)
    - Accurate line must exist and receipt must be valid
    - Item identity must match when both sides have accurate_item_id / master_item_id
    Does NOT write physical stock or Stock Saat Ini.
    """
    accurate_receipt_id = str(payload.get("accurate_receipt_id") or "").strip()
    accurate_line_id = str(payload.get("accurate_line_id") or "").strip()
    po_id = str(payload.get("purchase_order_id") or "").strip()
    po_line_id = str(payload.get("purchase_order_line_id") or "").strip()
    if not all([accurate_receipt_id, accurate_line_id, po_id, po_line_id]):
        raise HTTPException(400, "accurate_receipt_id, accurate_line_id, purchase_order_id, dan purchase_order_line_id wajib.")

    async with get_named_lock("purchase_goods_receipt"):
        idem = str(payload.get("idempotency_key") or "").strip()
        if idem:
            existing = await db[ALLOC_COLLECTION].find_one({"idempotency_key": idem}, {"_id": 0})
            if existing:
                return existing

        acc = await db.accurate_purchase_receipts.find_one(
            {"accurate_receipt_id": accurate_receipt_id}, {"_id": 0}
        )
        if not acc:
            raise HTTPException(400, f"Penerimaan Accurate '{accurate_receipt_id}' tidak ditemukan.")
        if acc.get("valid") is False:
            raise HTTPException(400, "Penerimaan Accurate tidak valid / nonaktif; alokasi ditolak.")

        acc_line = next(
            (
                li for li in (acc.get("lines") or [])
                if str(li.get("accurate_line_id") or "").strip() == accurate_line_id
            ),
            None,
        )
        if acc_line is None:
            raise HTTPException(400, "Baris penerimaan Accurate tidak ditemukan pada dokumen tersebut.")

        preview = await get_po_receipt_preview(db, po_id)
        src = next((l for l in preview["lines"] if str(l["purchase_order_line_id"]) == po_line_id), None)
        if src is None:
            raise HTTPException(400, "Baris Pesanan Pembelian tidak ditemukan.")

        # Item identity: reject mismatch when both sides carry the same ID kind.
        acc_aid = str(acc_line.get("accurate_item_id") or "").strip()
        src_aid = str(src.get("accurate_item_id") or "").strip()
        if acc_aid and src_aid and acc_aid != src_aid:
            raise HTTPException(
                400,
                f"Item tidak cocok: Accurate item '{acc_aid}' ≠ PO line item '{src_aid}'.",
            )
        acc_mid = str(acc_line.get("master_item_id") or "").strip()
        src_mid = str(src.get("master_item_id") or "").strip()
        if acc_mid and src_mid and acc_mid != src_mid:
            raise HTTPException(
                400,
                f"Item tidak cocok: master item '{acc_mid}' ≠ PO line master '{src_mid}'.",
            )

        prior_allocs = await db[ALLOC_COLLECTION].find(
            {
                "accurate_receipt_id": accurate_receipt_id,
                "accurate_line_id": accurate_line_id,
            },
            {"_id": 0, "allocated_qty": 1},
        ).to_list(5000)
        prior_allocated = sum(float(a.get("allocated_qty") or 0) for a in prior_allocs)
        remaining_accurate = remaining_qty(acc_line.get("received_qty"), prior_allocated)

        qty = parse_qty(payload.get("allocated_qty", payload.get("received_qty")), "Qty alokasi")
        remain_po = float(src["remaining_receivable_qty"] or 0)
        if not qty_le(qty, remain_po):
            raise HTTPException(400, f"Qty alokasi ({qty}) melebihi sisa PO ({remain_po}).")
        if not qty_le(qty, remaining_accurate):
            raise HTTPException(
                400,
                f"Qty alokasi ({qty}) melebihi sisa qty penerimaan Accurate ({remaining_accurate}).",
            )

        max_allocatable = min(remain_po, remaining_accurate)
        if not qty_le(qty, max_allocatable):
            raise HTTPException(
                400,
                f"Qty alokasi ({qty}) melebihi batas alokasi ({max_allocatable}).",
            )

        ts = now_iso()
        doc = {
            "id": str(uuid.uuid4()),
            "accurate_receipt_id": accurate_receipt_id,
            "accurate_line_id": accurate_line_id,
            "accurate_item_id": acc_aid,
            "master_item_id": src_mid or acc_mid,
            "purchase_order_id": preview["purchase_order_id"],
            "purchase_order_line_id": po_line_id,
            "po_number": preview["po_number"],
            "allocated_qty": qty,
            "unit_price": src["unit_price"],
            "receipt_date": str(payload.get("receipt_date") or ts[:10]).strip()[:10],
            "created_at": ts,
            "created_by": actor_snapshot(user),
        }
        if idem:
            doc["idempotency_key"] = idem
        await db[ALLOC_COLLECTION].insert_one(doc)
        doc.pop("_id", None)
        return doc
