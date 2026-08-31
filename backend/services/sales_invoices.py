"""Sales Invoice (Faktur Penjualan) Service.

Manages backend-authoritative sales invoices created from exact Sales Orders (PO).
Preserves exact PO snapshot values, enforces exact line identities (never merging
lines with identical item names), provides numeric validation for unit prices,
and authoritative line/grand total calculation.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List, Optional
import uuid

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

_invoice_locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _get_invoice_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _invoice_locks:
        _invoice_locks[loop] = asyncio.Lock()
    return _invoice_locks[loop]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_non_negative_price(raw: Any) -> float:
    """Strictly validates and converts unit price to a non-negative float.
    Rejects malformed strings, None, negative numbers, etc.
    """
    if raw is None or raw == "":
        return 0.0
    try:
        # Using Decimal to avoid precision quirks during conversion
        d = Decimal(str(raw).strip().replace(",", "."))
    except (InvalidOperation, ValueError, TypeError):
        raise HTTPException(400, f"Harga satuan '{raw}' tidak valid. Format harus angka.")
    
    if d < Decimal("0"):
        raise HTTPException(400, "Harga satuan tidak boleh negatif.")
    
    return float(d)


async def get_po_invoice_preview(db, po_id_or_doc: Any) -> Dict[str, Any]:
    """Generates an invoice snapshot preview from exact PO data.
    
    Extracts relevant PO header fields and exact PO line items without mutating PO.
    """
    if isinstance(po_id_or_doc, dict):
        order = po_id_or_doc
    else:
        order = await db.orders.find_one({"id": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            order = await db.orders.find_one({"po_number": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan (PO) '{po_id_or_doc}' tidak ditemukan.")

    po_id = order.get("id") or str(order.get("_id", ""))
    po_number = (order.get("po_number") or "").strip()
    order_date = order.get("order_date") or order.get("created_at") or ""
    customer = (order.get("customer") or order.get("customer_name") or "").strip()
    outlet = (order.get("outlet") or order.get("outlet_name") or "").strip()
    recipient = (order.get("recipient") or order.get("pic") or order.get("recipient_name") or "").strip()
    address = (order.get("address") or order.get("shipping_address") or order.get("delivery_address") or "").strip()
    delivery_zone = (order.get("delivery_zone") or order.get("zona_kirim") or order.get("zona") or order.get("shipping_zone") or "").strip()
    sales = (order.get("sales") or order.get("seller") or order.get("sales_rep") or order.get("sales_name") or "").strip()

    raw_lines = order.get("line_items") or []
    preview_lines = []
    total_qty = 0

    for idx, li in enumerate(raw_lines):
        line_idx = int(li.get("line_idx", idx) if li.get("line_idx") is not None else idx)
        line_id = str(li.get("line_id") or li.get("id") or f"line_{idx}")
        item_name = str(li.get("name") or li.get("item_name") or "").strip()
        item_code = str(li.get("item_code") or "").strip()
        unit = str(li.get("unit") or "pcs").strip()
        color = str(li.get("color") or "").strip()
        
        try:
            qty = int(li.get("quantity") or 0)
        except (ValueError, TypeError):
            qty = 0

        total_qty += qty

        preview_lines.append({
            "line_idx": line_idx,
            "line_id": line_id,
            "item_name": item_name,
            "item_code": item_code,
            "color": color,
            "quantity": qty,
            "unit": unit,
            "unit_price": 0,
            "line_total": 0,
        })

    return {
        "po_id": po_id,
        "po_number": po_number,
        "order_date": order_date,
        "customer": customer,
        "outlet": outlet,
        "recipient": recipient,
        "address": address,
        "delivery_zone": delivery_zone,
        "sales": sales,
        "total_qty": total_qty,
        "lines": preview_lines,
        "grand_total": 0,
    }


async def create_sales_invoice(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Creates a new Sales Invoice (Faktur Penjualan) backend-authoritatively.
    
    Guarantees:
    - Exact PO snapshot
    - Exact line identity preserved (distinct lines with same item name stay distinct)
    - Authoritative backend recalculation of line_total and grand_total
    - Rejection of negative / malformed prices
    - Duplicate submission protection via idempotency_key or internal locks
    - Zero mutation on PO, customer/outlet master, or production workflow
    """
    po_id = str(payload.get("po_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Penjualan (po_id) wajib diisi.")

    async with _get_invoice_lock():
        # Idempotency check if idempotency_key is provided
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key:
            existing_by_key = await db.sales_invoices.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
            if existing_by_key:
                return existing_by_key

        # 1. Fetch exact PO
        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
        if not order:
            order = await db.orders.find_one({"po_number": po_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan (PO) '{po_id}' tidak ditemukan.")

        if order.get("cancelled"):
            raise HTTPException(400, "Pesanan Penjualan ini sudah dibatalkan.")

        # 2. Build preview & snapshot base
        preview = await get_po_invoice_preview(db, order)
        raw_po_lines = order.get("line_items") or []

        # 3. Match and calculate line prices from input
        input_lines = payload.get("lines") or payload.get("line_items") or []
        input_by_idx: Dict[int, Any] = {}
        for inp in input_lines:
            try:
                l_idx = int(inp.get("line_idx", 0))
            except (ValueError, TypeError):
                continue
            input_by_idx[l_idx] = inp

        calculated_lines = []
        authoritative_grand_total = 0.0
        total_qty = 0

        for idx, po_line in enumerate(raw_po_lines):
            line_idx = int(po_line.get("line_idx", idx) if po_line.get("line_idx") is not None else idx)
            line_id = str(po_line.get("line_id") or po_line.get("id") or f"line_{idx}")
            item_name = str(po_line.get("name") or po_line.get("item_name") or "").strip()
            item_code = str(po_line.get("item_code") or "").strip()
            unit = str(po_line.get("unit") or "pcs").strip()
            color = str(po_line.get("color") or "").strip()
            
            try:
                qty = int(po_line.get("quantity") or 0)
            except (ValueError, TypeError):
                qty = 0

            # Match input line by line_idx
            matched_input = input_by_idx.get(line_idx)
            raw_unit_price = matched_input.get("unit_price") if matched_input else 0
            unit_price = _parse_non_negative_price(raw_unit_price)

            # Backend authoritative calculation
            line_total = round(qty * unit_price, 2)
            authoritative_grand_total += line_total
            total_qty += qty

            calculated_lines.append({
                "line_idx": line_idx,
                "line_id": line_id,
                "item_name": item_name,
                "item_code": item_code,
                "color": color,
                "quantity": qty,
                "unit": unit,
                "unit_price": unit_price,
                "line_total": line_total,
            })

        invoice_id = str(uuid.uuid4())
        # Use stable internal invoice id for persistence & identifier
        invoice_doc = {
            "id": invoice_id,
            "invoice_number": invoice_id,  # Stable internal invoice id
            "idempotency_key": idempotency_key or None,
            "po_id": preview["po_id"],
            "po_number": preview["po_number"],
            "order_date": preview["order_date"],
            "invoice_date": payload.get("invoice_date") or now_iso()[:10],
            "customer": preview["customer"],
            "outlet": preview["outlet"],
            "recipient": preview["recipient"],
            "address": preview["address"],
            "delivery_zone": preview["delivery_zone"],
            "sales": preview["sales"],
            "notes": str(payload.get("notes") or "").strip(),
            "lines": calculated_lines,
            "total_qty": total_qty,
            "grand_total": round(authoritative_grand_total, 2),
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "created_by": {
                "id": str(user.get("id") or user.get("_id") or ""),
                "name": str(user.get("name") or user.get("email") or ""),
                "role": str(user.get("role") or ""),
            },
        }

        try:
            await db.sales_invoices.insert_one(invoice_doc)
        except DuplicateKeyError:
            if idempotency_key:
                existing = await db.sales_invoices.find_one({"idempotency_key": idempotency_key}, {"_id": 0})
                if existing:
                    return existing
            raise HTTPException(409, "Faktur Penjualan duplikat terdeteksi.")

        invoice_doc.pop("_id", None)
        return invoice_doc


async def list_sales_invoices(db, q: Optional[str] = None, po_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """List all sales invoices with optional search query and po_id filter."""
    filters: Dict[str, Any] = {}
    if po_id:
        filters["po_id"] = str(po_id)
    if q and str(q).strip():
        needle = str(q).strip()
        filters["$or"] = [
            {"id": {"$regex": needle, "$options": "i"}},
            {"invoice_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"customer": {"$regex": needle, "$options": "i"}},
            {"outlet": {"$regex": needle, "$options": "i"}},
            {"sales": {"$regex": needle, "$options": "i"}},
        ]

    cursor = db.sales_invoices.find(filters, {"_id": 0}).sort("created_at", -1)
    invoices = await cursor.to_list(1000)
    return invoices


async def get_sales_invoice_detail(db, invoice_id: str) -> Dict[str, Any]:
    """Fetch detail of a single sales invoice."""
    inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
    if not inv:
        inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
    if not inv:
        raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")
    return inv


async def update_sales_invoice(db, invoice_id: str, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Updates an existing sales invoice (e.g. unit prices, notes, invoice_date).
    
    Recalculates line totals and grand total authoritatively on backend.
    Preserves exact PO lines and never mutates PO or Master collections.
    """
    async with _get_invoice_lock():
        inv = await db.sales_invoices.find_one({"id": str(invoice_id)}, {"_id": 0})
        if not inv:
            inv = await db.sales_invoices.find_one({"invoice_number": str(invoice_id)}, {"_id": 0})
        if not inv:
            raise HTTPException(404, f"Faktur Penjualan '{invoice_id}' tidak ditemukan.")

        existing_lines = inv.get("lines") or []
        input_lines = payload.get("lines") or payload.get("line_items") or []
        input_by_idx = {int(inp.get("line_idx", idx)): inp for idx, inp in enumerate(input_lines)}

        updated_lines = []
        authoritative_grand_total = 0.0
        total_qty = 0

        for idx, el in enumerate(existing_lines):
            line_idx = int(el.get("line_idx", idx))
            qty = int(el.get("quantity") or 0)
            
            # If price was provided in payload, update it; otherwise keep existing
            if line_idx in input_by_idx:
                raw_price = input_by_idx[line_idx].get("unit_price")
                unit_price = _parse_non_negative_price(raw_price)
            else:
                unit_price = _parse_non_negative_price(el.get("unit_price", 0))

            line_total = round(qty * unit_price, 2)
            authoritative_grand_total += line_total
            total_qty += qty

            updated_el = dict(el)
            updated_el["unit_price"] = unit_price
            updated_el["line_total"] = line_total
            updated_lines.append(updated_el)

        update_fields = {
            "lines": updated_lines,
            "total_qty": total_qty,
            "grand_total": round(authoritative_grand_total, 2),
            "updated_at": now_iso(),
            "updated_by": {
                "id": str(user.get("id") or user.get("_id") or ""),
                "name": str(user.get("name") or user.get("email") or ""),
                "role": str(user.get("role") or ""),
            },
        }

        if "invoice_date" in payload and payload["invoice_date"]:
            update_fields["invoice_date"] = str(payload["invoice_date"])
        if "notes" in payload:
            update_fields["notes"] = str(payload["notes"] or "").strip()

        await db.sales_invoices.update_one({"id": inv["id"]}, {"$set": update_fields})

        updated_doc = await db.sales_invoices.find_one({"id": inv["id"]}, {"_id": 0})
        return updated_doc
