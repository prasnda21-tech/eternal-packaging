"""Sales Delivery (Pengiriman Pesanan) Service.

Manages backend-authoritative sales delivery notes (Surat Jalan Penjualan)
linked stably to Sales Orders (Pesanan Penjualan / PO).
Supports partial shipments, multi-line integrity, concurrency/duplicate safety,
and PDF document rendering.
"""
from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from quantity_rules import packing_lines_stored, qty_packed_for_line, qty_shipped_for_line
from services import storage as obj_storage
from services.surat_jalan_files import (
    SOURCE_SYSTEM_GENERATED,
    SOURCE_TYPE_PENGIRIMAN,
    find_generated_sj_file,
    generated_file_is_real,
    generated_sj_file_id,
)

logger = logging.getLogger(__name__)

_delivery_locks: Dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}


def _get_delivery_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    if loop not in _delivery_locks:
        _delivery_locks[loop] = asyncio.Lock()
    return _delivery_locks[loop]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def derive_base_sj_number(po_number: str) -> str:
    """Derives base SJ number from PO number while preserving main sequence."""
    pn = (po_number or "").strip()
    if not pn:
        return "SJ/" + datetime.now(timezone.utc).strftime("%d/%m/%y/001")
    
    # 1. Match PO/04/09/26/001 or SO/04/09/26/001 -> SJ/04/09/26/001
    # 2. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> SJ/2026/08/30/000123
    # 3. Match PO-20260830-001 or SO-20260830-001 -> SJ-20260830-001
    # 4. Match PO.2026.08.001 -> SJ.2026.08.001
    # 5. Match PO 12345 -> SJ 12345
    # 6. Match PO12345 -> SJ12345
    m = re.match(r'^(?:PO|SO)([\/\.\-\s_]?)(.*)$', pn, re.IGNORECASE)
    if m:
        sep = m.group(1)
        rest = m.group(2)
        return f"SJ{sep}{rest}"
    
    # Check other alphabetic prefixes with separator
    m2 = re.match(r'^[A-Za-z]+([\/\.\-\s_])(.*)$', pn)
    if m2:
        sep = m2.group(1)
        rest = m2.group(2)
        return f"SJ{sep}{rest}"
    
    # Plain number or other pattern
    return f"SJ-{pn}"


def derive_sj_number(po_number: str, shipment_index: int) -> str:
    """Computes backend-authoritative SJ number for a specific shipment index.
    
    Index 1: base SJ (e.g. SJ/04/09/26/001 or SJ-20260830-001)
    Index 2: base SJ + "_2"
    Index 3: base SJ + "_3"
    """
    base = derive_base_sj_number(po_number)
    if shipment_index <= 1:
        return base
    return f"{base}_{shipment_index}"


def delivery_lines_fingerprint(lines: List[dict]) -> Tuple[Tuple[int, int, str], ...]:
    """Exact-line identity fingerprint. Same-name products stay separate."""
    rows = []
    for ln in lines or []:
        if not isinstance(ln, dict):
            continue
        try:
            qty = int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity") or 0)
        except (ValueError, TypeError):
            qty = 0
        if qty <= 0:
            continue
        try:
            idx = int(ln.get("line_idx", 0))
        except (ValueError, TypeError):
            idx = 0
        rows.append((idx, qty, str(ln.get("line_id") or "")))
    return tuple(sorted(rows))


def build_generated_sj_file_entry(delivery: dict, user: dict, storage_path: str, created_at: str) -> dict:
    file_id = generated_sj_file_id(delivery.get("id") or delivery.get("delivery_id"))
    sj_number = str(delivery.get("sj_number") or "").strip()
    lines = []
    for ln in delivery.get("line_items") or []:
        if not isinstance(ln, dict):
            continue
        try:
            qty = int(ln.get("qty") if ln.get("qty") is not None else ln.get("quantity") or 0)
        except (ValueError, TypeError):
            qty = 0
        if qty <= 0:
            continue
        try:
            idx = int(ln.get("line_idx", 0))
        except (ValueError, TypeError):
            idx = 0
        lines.append({
            "line_idx": idx,
            "line_id": ln.get("line_id"),
            "qty": qty,
            "name": ln.get("name"),
            "unit": ln.get("unit") or "pcs",
            "item_code": ln.get("item_code"),
            "color": ln.get("color"),
            "production_process": ln.get("production_process"),
        })
    return {
        "id": file_id,
        "name": f"Surat Jalan {sj_number}.pdf" if sj_number else "Surat Jalan.pdf",
        "content_type": "application/pdf",
        "uploaded_by": (user or {}).get("email") or "system",
        "uploaded_by_name": (user or {}).get("name") or (user or {}).get("email") or "Sistem",
        "uploaded_by_role": (user or {}).get("role") or "",
        "uploaded_at": created_at,
        "shipment_id": file_id,
        "sj_number": sj_number or None,
        "lines": lines,
        "source": SOURCE_SYSTEM_GENERATED,
        "source_type": SOURCE_TYPE_PENGIRIMAN,
        "storage_path": storage_path,
    }


async def ensure_generated_sj_pdf(db, order: dict, delivery: dict, user: dict) -> Tuple[Optional[dict], Optional[str]]:
    """Generate the real SJ PDF, store it, and register the authoritative file.

    Idempotent: same shipment always maps to the same file id / object key.
    Never writes fake surat_jalan_files metadata when storage/PDF fails.
    """
    shipment_id = str(delivery.get("id") or delivery.get("delivery_id") or "").strip()
    po_id = str(delivery.get("po_id") or (order or {}).get("id") or "").strip()
    if not shipment_id or not po_id:
        return None, "shipment_identity_missing"

    fresh = await db.orders.find_one({"id": po_id}, {"_id": 0}) or order or {}
    existing = find_generated_sj_file(fresh, shipment_id)
    if generated_file_is_real(existing):
        rec = await db.files.find_one({"id": existing.get("id"), "is_deleted": {"$ne": True}}, {"_id": 0})
        if rec and rec.get("storage_path"):
            return existing, None

    try:
        pdf_bytes = generate_sales_delivery_pdf_bytes(delivery, None)
    except Exception:
        logger.exception("Failed to generate Surat Jalan PDF for shipment %s", shipment_id)
        return None, "pdf_generate_failed"

    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        logger.error("Generated Surat Jalan bytes are not a real PDF for shipment %s", shipment_id)
        return None, "pdf_invalid"

    file_id = generated_sj_file_id(shipment_id)
    storage_path = f"{obj_storage.APP_NAME}/surat-jalan/{po_id}/{file_id}.pdf"
    try:
        stored = obj_storage.put_object(storage_path, pdf_bytes, "application/pdf")
        storage_path = stored.get("path") or storage_path
    except Exception:
        logger.exception("Surat Jalan object storage write failed for shipment %s", shipment_id)
        return None, "storage_failed"

    stamp = now_iso()
    entry = build_generated_sj_file_entry(delivery, user or {}, storage_path, stamp)
    file_doc = {
        "id": file_id,
        "storage_path": storage_path,
        "original_filename": entry["name"],
        "content_type": "application/pdf",
        "size": (stored.get("size") if isinstance(stored, dict) else None) or len(pdf_bytes),
        "kind": "surat_jalan",
        "order_id": po_id,
        "shipment_id": shipment_id,
        "sj_number": entry.get("sj_number"),
        "source": SOURCE_SYSTEM_GENERATED,
        "source_type": SOURCE_TYPE_PENGIRIMAN,
        "uploaded_by": entry.get("uploaded_by"),
        "uploaded_at": stamp,
        "is_deleted": False,
    }
    try:
        await db.files.insert_one(file_doc)
    except DuplicateKeyError:
        await db.files.update_one(
            {"id": file_id},
            {"$set": {
                "storage_path": storage_path,
                "original_filename": entry["name"],
                "content_type": "application/pdf",
                "size": file_doc["size"],
                "kind": "surat_jalan",
                "order_id": po_id,
                "shipment_id": shipment_id,
                "sj_number": entry.get("sj_number"),
                "source": SOURCE_SYSTEM_GENERATED,
                "source_type": SOURCE_TYPE_PENGIRIMAN,
                "is_deleted": False,
            }},
        )

    await db.orders.update_one(
        {"id": po_id, "surat_jalan_files.id": {"$ne": file_id}},
        {"$push": {"surat_jalan_files": entry}},
    )
    await db.orders.update_one(
        {"id": po_id, "surat_jalan_files.id": file_id},
        {"$set": {
            "surat_jalan_files.$.storage_path": storage_path,
            "surat_jalan_files.$.content_type": "application/pdf",
            "surat_jalan_files.$.name": entry["name"],
            "surat_jalan_files.$.source": SOURCE_SYSTEM_GENERATED,
            "surat_jalan_files.$.source_type": SOURCE_TYPE_PENGIRIMAN,
            "surat_jalan_files.$.shipment_id": shipment_id,
            "surat_jalan_files.$.sj_number": entry.get("sj_number"),
            "surat_jalan_files.$.lines": entry.get("lines") or [],
        }},
    )
    await db.orders.update_one(
        {"id": po_id},
        {"$set": {"shipments.$[sh].file_id": file_id}},
        array_filters=[{"sh.id": shipment_id}],
    )
    await db.sales_deliveries.update_one(
        {"id": shipment_id},
        {"$set": {"generated_sj_file_id": file_id, "updated_at": stamp}},
    )
    return entry, None


async def _reuse_existing_delivery_if_retry(db, order: dict, validated_lines: List[dict], user: dict) -> Optional[dict]:
    """If a matching shipment exists without a real generated PDF, complete that document.

    Does not collapse later legitimate partials that happen to use the same qty.
    """
    want = delivery_lines_fingerprint(validated_lines)
    if not want:
        return None
    po_id = str(order.get("id") or "")
    existing = await db.sales_deliveries.find(
        {"po_id": po_id, "is_cancelled": {"$ne": True}},
        {"_id": 0},
    ).sort("shipment_index", 1).to_list(1000)
    if not existing:
        return None

    fresh_order = await db.orders.find_one({"id": po_id}, {"_id": 0}) or order
    for d in existing:
        if delivery_lines_fingerprint(d.get("line_items")) != want:
            continue
        gen = find_generated_sj_file(fresh_order, d.get("id"))
        if generated_file_is_real(gen):
            continue
        entry, _err = await ensure_generated_sj_pdf(db, fresh_order, d, user)
        if entry:
            d["generated_sj_file_id"] = entry["id"]
        return d
    return None


async def get_po_delivery_summary(db, po_id_or_doc: Any) -> Dict[str, Any]:
    """Calculate delivered & remaining quantities per line item for a PO.
    
    Priority for header fields:
    1. Value snapshot from exact PO
    2. Fallback to master customer/outlet if PO field is empty
    3. Empty string if not found anywhere (allows manual entry)
    """
    if isinstance(po_id_or_doc, dict):
        order = po_id_or_doc
    else:
        order = await db.orders.find_one({"id": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            # Also try po_number lookup if uuid lookup fails
            order = await db.orders.find_one({"po_number": str(po_id_or_doc)}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan (PO) '{po_id_or_doc}' tidak ditemukan.")

    po_id = order.get("id")
    po_number = order.get("po_number")

    # 1. Base snapshot values from PO
    customer_val = (order.get("customer") or order.get("customer_name") or "").strip()
    outlet_val = (order.get("outlet") or order.get("outlet_name") or "").strip()
    recipient_val = (order.get("recipient") or order.get("pic") or order.get("recipient_name") or "").strip()
    address_val = (order.get("address") or order.get("shipping_address") or order.get("delivery_address") or "").strip()
    zone_val = (order.get("delivery_zone") or order.get("zona_kirim") or order.get("zona") or order.get("shipping_zone") or "").strip()
    sales_val = (order.get("sales") or order.get("seller") or order.get("sales_rep") or order.get("sales_name") or "").strip()
    expedition_val = (order.get("expedition") or order.get("ekspedisi") or order.get("expedition_or_vehicle") or "Internal fleet").strip()
    notes_val = (order.get("notes") or order.get("delivery_note") or "").strip()

    # 2. Fallback to master delivery_outlets or recent orders if fields are empty
    if customer_val and (not address_val or not outlet_val or not recipient_val or not zone_val):
        master_outlet = None
        if outlet_val:
            master_outlet = await db.delivery_outlets.find_one({
                "customer_name": {"$regex": f"^{re.escape(customer_val)}$", "$options": "i"},
                "name": {"$regex": f"^{re.escape(outlet_val)}$", "$options": "i"}
            }, {"_id": 0})
        if not master_outlet:
            master_outlet = await db.delivery_outlets.find_one({
                "customer_name": {"$regex": f"^{re.escape(customer_val)}$", "$options": "i"}
            }, {"_id": 0})

        if master_outlet:
            if not address_val:
                address_val = (master_outlet.get("formatted_address") or master_outlet.get("address") or "").strip()
            if not outlet_val:
                outlet_val = (master_outlet.get("name") or "").strip()
            if not recipient_val:
                recipient_val = (master_outlet.get("recipient") or "").strip()
            if not zone_val:
                zone_val = (master_outlet.get("delivery_zone") or "").strip()

        # Secondary fallback: check recent order history for this customer
        if not address_val or not recipient_val or not zone_val or not sales_val:
            try:
                recent_cust_po = await db.orders.find_one(
                    {
                        "customer": {"$regex": f"^{re.escape(customer_val)}$", "$options": "i"},
                        "cancelled": {"$ne": True},
                        "id": {"$ne": po_id}
                    },
                    {"_id": 0},
                    sort=[("created_at", -1)]
                )
            except Exception:
                recent_cust_po = None
            
            if recent_cust_po:
                if not address_val:
                    address_val = (recent_cust_po.get("address") or "").strip()
                if not recipient_val:
                    recipient_val = (recent_cust_po.get("recipient") or "").strip()
                if not zone_val:
                    zone_val = (recent_cust_po.get("delivery_zone") or "").strip()
                if not sales_val:
                    sales_val = (recent_cust_po.get("sales") or recent_cust_po.get("seller") or "").strip()

    # Fetch all existing sales deliveries for this exact PO sorted by shipment_index
    deliveries_cursor = db.sales_deliveries.find({"po_id": po_id, "is_cancelled": {"$ne": True}}, {"_id": 0}).sort("shipment_index", 1)
    existing_deliveries = await deliveries_cursor.to_list(1000)

    # Accumulate shipped quantities per line_idx / line_id
    shipped_by_line_idx: Dict[int, int] = {}
    for d in existing_deliveries:
        for li in d.get("line_items", []):
            l_idx = int(li.get("line_idx", 0))
            q = int(li.get("qty") if li.get("qty") is not None else li.get("quantity", 0))
            shipped_by_line_idx[l_idx] = shipped_by_line_idx.get(l_idx, 0) + q

    # Build line item summary
    raw_lines = order.get("line_items") or []
    lines_summary = []
    total_ordered = 0
    total_shipped = 0

    for idx, li in enumerate(raw_lines):
        ordered_q = int(li.get("quantity") or 0)
        shipped_q = shipped_by_line_idx.get(idx, 0)
        remain_q = max(0, ordered_q - shipped_q)
        
        total_ordered += ordered_q
        total_shipped += shipped_q

        stable_line_id = li.get("line_id") or f"{po_id}_line_{idx}"
        lines_summary.append({
            "line_idx": idx,
            "line_id": stable_line_id,
            "item_code": li.get("item_code"),
            "name": li.get("name") or f"Item #{idx+1}",
            "unit": li.get("unit") or "pcs",
            "color": li.get("color"),
            "production_process": li.get("production_process") or "print",
            "ordered_qty": ordered_q,
            "shipped_qty": shipped_q,
            "remaining_qty": remain_q,
            "notes": li.get("notes") or "",
        })

    total_remaining = max(0, total_ordered - total_shipped)
    is_cancelled = bool(order.get("cancelled"))

    all_lines_fulfilled = len(raw_lines) > 0 and all(
        shipped_by_line_idx.get(idx, 0) >= int(li.get("quantity") or 0)
        for idx, li in enumerate(raw_lines)
    )

    if is_cancelled:
        delivery_status = "DIBATALKAN"
    elif all_lines_fulfilled or (total_ordered > 0 and total_shipped >= total_ordered):
        delivery_status = "SELESAI"
    else:
        delivery_status = "SEBAGIAN"

    next_shipment_index = len(existing_deliveries) + 1
    suggested_sj = derive_sj_number(po_number, next_shipment_index)

    return {
        "po_id": po_id,
        "po_number": po_number,
        "order_date": order.get("order_date"),
        "customer": customer_val,
        "customer_name": customer_val,
        "outlet": outlet_val,
        "outlet_name": outlet_val,
        "recipient": recipient_val,
        "recipient_name": recipient_val,
        "address": address_val,
        "shipping_address": address_val,
        "delivery_zone": zone_val,
        "shipping_zone": zone_val,
        "sales": sales_val,
        "sales_name": sales_val,
        "expedition": expedition_val,
        "expedition_or_vehicle": expedition_val,
        "notes": notes_val,
        "delivery_note": notes_val,
        "created_by_user": order.get("created_by_user") or order.get("imported_by_name") or "",
        "lines": lines_summary,
        "total_ordered": total_ordered,
        "total_shipped": total_shipped,
        "total_remaining": total_remaining,
        "delivery_status": delivery_status,
        "next_shipment_index": next_shipment_index,
        "suggested_sj_number": suggested_sj,
        "can_create_delivery": (not is_cancelled and total_remaining > 0),
        "deliveries": existing_deliveries,
    }


async def create_sales_delivery(db, payload: Dict[str, Any], user: dict) -> Dict[str, Any]:
    """Create a new Sales Delivery (Pengiriman Pesanan) backend-authoritatively.
    
    Guarantees:
    - Stable relations (po_id, delivery_id, stable line items)
    - Concurrency and duplicate safety
    - Strict over-shipment prevention per exact PO line
    - Exact SJ numbering formula (SJ base, SJ base-2, SJ base-3...)
    - Preserves original PO unchanged
    - Saves final edited snapshot values
    """
    po_id = str(payload.get("po_id") or "").strip()
    if not po_id:
        raise HTTPException(400, "ID Pesanan Penjualan (po_id) wajib diisi.")

    async with _get_delivery_lock():
        # 1. Fetch current PO state
        order = await db.orders.find_one({"id": po_id}, {"_id": 0})
        if not order:
            order = await db.orders.find_one({"po_number": po_id}, {"_id": 0})
        if not order:
            raise HTTPException(404, f"Pesanan Penjualan dengan ID '{po_id}' tidak ditemukan.")

        if order.get("cancelled"):
            raise HTTPException(400, "Pesanan Penjualan ini sudah dibatalkan.")

        # 2. Get latest delivery summary and remaining quantities
        summary = await get_po_delivery_summary(db, order)
        lines_summary_by_idx = {l["line_idx"]: l for l in summary["lines"]}

        # 3. Validate line items payload
        input_lines = payload.get("line_items") or []
        if not input_lines:
            raise HTTPException(400, "Minimal 1 line item pengiriman harus memiliki kuantitas > 0.")

        validated_lines = []
        total_delivery_qty = 0

        for item in input_lines:
            try:
                line_idx = int(item.get("line_idx", 0))
            except (ValueError, TypeError):
                line_idx = 0

            target_line = lines_summary_by_idx.get(line_idx)
            if not target_line:
                raise HTTPException(400, f"Line item #{line_idx + 1} tidak ditemukan pada Pesanan Penjualan.")

            # Reject client attempt to spoof or alter exact line identity
            client_line_id = str(item.get("line_id") or "").strip()
            if client_line_id and client_line_id != str(target_line["line_id"]):
                raise HTTPException(400, f"Identitas line item #{line_idx + 1} tidak cocok dengan PO.")

            try:
                qty_to_ship = int(item.get("qty") if item.get("qty") is not None else item.get("quantity", 0))
            except (ValueError, TypeError):
                qty_to_ship = 0

            if qty_to_ship <= 0:
                continue

            total_delivery_qty += qty_to_ship
            validated_lines.append({
                "line_idx": line_idx,
                "line_id": target_line["line_id"],
                "item_code": target_line["item_code"],
                "name": target_line["name"],
                "unit": target_line["unit"],
                "color": target_line["color"],
                "production_process": target_line["production_process"],
                "ordered_qty": target_line["ordered_qty"],
                "qty": qty_to_ship,
                "notes": (item.get("notes") or "").strip(),
            })

        if not validated_lines or total_delivery_qty <= 0:
            raise HTTPException(400, "Total kuantitas pengiriman sekarang harus lebih besar dari 0.")

        reused = await _reuse_existing_delivery_if_retry(db, order, validated_lines, user)
        if reused:
            return reused

        if not summary["can_create_delivery"]:
            raise HTTPException(400, "Seluruh kuantitas Pesanan Penjualan ini sudah selesai dibuatkan Pengiriman Pesanan.")

        for item in validated_lines:
            line_idx = item["line_idx"]
            target_line = lines_summary_by_idx[line_idx]
            qty_to_ship = item["qty"]
            remain = target_line["remaining_qty"]
            if qty_to_ship > remain:
                raise HTTPException(
                    400,
                    f"Kuantitas pengiriman untuk '{target_line['name']}' ({qty_to_ship} {target_line['unit']}) "
                    f"melebihi sisa PO yang tersedia ({remain} {target_line['unit']})."
                )

            # Workflow gate: if packing lines are tracked on this PO, ensure quantity has passed Packing
            if packing_lines_stored(order) is not None:
                packed = qty_packed_for_line(order, line_idx)
                shipped = qty_shipped_for_line(order, line_idx)
                avail_packing = max(0, packed - shipped)
                if qty_to_ship > avail_packing:
                    raise HTTPException(
                        400,
                        f"Kuantitas pengiriman untuk '{target_line['name']}' ({qty_to_ship} {target_line['unit']}) "
                        f"melebihi kuantitas packing yang siap dibuatkan Surat Jalan ({avail_packing} {target_line['unit']})."
                    )

        # 4. Determine shipment index and SJ number backend-authoritatively
        now = now_iso()
        delivery_date = str(payload.get("delivery_date") or now[:10]).strip()
        po_number = order.get("po_number")

        # Resolve edited snapshot values with fallback to PO snapshot
        final_customer = summary["customer"]
        final_outlet = str(payload.get("outlet") if payload.get("outlet") is not None else summary["outlet"]).strip()
        final_recipient = str(payload.get("recipient") if payload.get("recipient") is not None else summary["recipient"]).strip()
        final_address = str(payload.get("address") if payload.get("address") is not None else summary["address"]).strip()
        final_zone = str(payload.get("delivery_zone") if payload.get("delivery_zone") is not None else summary["delivery_zone"]).strip()
        final_sales = str(payload.get("sales") if payload.get("sales") is not None else summary["sales"]).strip()
        final_expedition = str(payload.get("expedition") if payload.get("expedition") is not None else summary["expedition"]).strip()
        final_notes = str(payload.get("notes") if payload.get("notes") is not None else "").strip()

        # Retry loop in case of race condition / concurrent submission
        max_retries = 3
        for attempt in range(max_retries):
            # Count existing valid deliveries to determine authoritative shipment_index
            existing_count = await db.sales_deliveries.count_documents({
                "po_id": summary["po_id"],
                "is_cancelled": {"$ne": True}
            })
            shipment_index = existing_count + 1
            authoritative_sj = derive_sj_number(po_number, shipment_index)

            delivery_id = str(uuid.uuid4())
            delivery_doc = {
                "id": delivery_id,
                "delivery_id": delivery_id,
                "po_id": summary["po_id"],
                "po_number": po_number,
                "sj_number": authoritative_sj,
                "shipment_index": shipment_index,
                "delivery_date": delivery_date,
                "customer": final_customer,
                "customer_name": final_customer,
                "outlet": final_outlet,
                "outlet_name": final_outlet,
                "recipient": final_recipient,
                "recipient_name": final_recipient,
                "address": final_address,
                "shipping_address": final_address,
                "delivery_zone": final_zone,
                "shipping_zone": final_zone,
                "sales": final_sales,
                "sales_name": final_sales,
                "expedition": final_expedition,
                "expedition_or_vehicle": final_expedition,
                "notes": final_notes,
                "delivery_note": final_notes,
                "created_by": user.get("email") or "",
                "created_by_name": user.get("name") or user.get("email") or "",
                "created_by_role": user.get("role") or "",
                "created_at": now,
                "updated_at": now,
                "line_items": validated_lines,
                "total_qty": total_delivery_qty,
                "item_count": len(validated_lines),
                "status": "SELESAI" if (summary["total_remaining"] - total_delivery_qty <= 0) else "SEBAGIAN",
                "is_cancelled": False,
            }

            try:
                await db.sales_deliveries.insert_one(delivery_doc)
                delivery_doc.pop("_id", None)

                # Auto-register corresponding Surat Jalan into PO records
                shipment_entry = {
                    "id": delivery_id,
                    "number": authoritative_sj,
                    "shipment_index": shipment_index,
                    "delivery_date": delivery_date,
                    "customer": final_customer,
                    "outlet": final_outlet,
                    "sales": final_sales,
                    "recipient": final_recipient,
                    "address": final_address,
                    "delivery_zone": final_zone,
                    "expedition": final_expedition,
                    "notes": final_notes,
                    "lines": [
                        {
                            "line_idx": ln["line_idx"],
                            "line_id": ln["line_id"],
                            "qty": ln["qty"],
                            "name": ln["name"],
                            "unit": ln.get("unit") or "pcs",
                            "color": ln.get("color"),
                            "production_process": ln.get("production_process"),
                        }
                        for ln in validated_lines
                    ],
                    "total_qty": total_delivery_qty,
                    "created_at": now,
                    "created_by": user.get("email") or "",
                    "sales_delivery_id": delivery_id,
                }

                await db.orders.update_one(
                    {"id": summary["po_id"], "shipments.id": {"$ne": delivery_id}},
                    {
                        "$push": {
                            "shipments": shipment_entry,
                        },
                        "$set": {
                            "delivery_note_number": authoritative_sj,
                            "delivery_note_created_at": now,
                            "delivery_note_created_by": user.get("email") or "",
                            "delivery_note_created_by_name": user.get("name") or user.get("email") or "",
                        }
                    }
                )

                file_entry, pdf_err = await ensure_generated_sj_pdf(db, order, delivery_doc, user)
                if file_entry:
                    delivery_doc["generated_sj_file_id"] = file_entry["id"]
                elif pdf_err:
                    delivery_doc["generated_sj_file_error"] = pdf_err
                    logger.warning(
                        "Pengiriman %s saved without generated SJ PDF (%s); retry can complete the document",
                        authoritative_sj,
                        pdf_err,
                    )

                return delivery_doc
            except DuplicateKeyError:
                # Concurrent submit detected: re-fetch summary and retry
                fresh_summary = await get_po_delivery_summary(db, order)
                if not fresh_summary["can_create_delivery"]:
                    raise HTTPException(400, "Kuantitas PO ini sudah dipenuhi oleh pengiriman lain yang selesai terlebih dahulu.")
                if attempt == max_retries - 1:
                    raise HTTPException(409, "Terjadi konflik transaksi bersamaan. Silakan muat ulang dan coba lagi.")

        raise HTTPException(500, "Gagal membuat Pengiriman Pesanan.")


async def list_sales_deliveries(
    db,
    q: Optional[str] = None,
    po_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[dict]:
    """Query list of sales deliveries with search and filter."""
    query: dict = {"is_cancelled": {"$ne": True}}

    if po_id and po_id.strip():
        query["po_id"] = po_id.strip()

    if q and q.strip():
        needle = re.escape(q.strip())
        query["$or"] = [
            {"sj_number": {"$regex": needle, "$options": "i"}},
            {"po_number": {"$regex": needle, "$options": "i"}},
            {"customer": {"$regex": needle, "$options": "i"}},
            {"outlet": {"$regex": needle, "$options": "i"}},
            {"recipient": {"$regex": needle, "$options": "i"}},
            {"sales": {"$regex": needle, "$options": "i"}},
        ]

    deliveries = await db.sales_deliveries.find(query, {"_id": 0}).sort("created_at", -1).to_list(1000)

    # Derive authoritative status from parent PO
    po_ids = list({d["po_id"] for d in deliveries if d.get("po_id")})
    po_statuses: Dict[str, str] = {}
    if po_ids:
        orders_cursor = db.orders.find({"id": {"$in": po_ids}}, {"_id": 0})
        orders_map = {o["id"]: o async for o in orders_cursor}

        pipe = [
            {"$match": {"po_id": {"$in": po_ids}, "is_cancelled": {"$ne": True}}},
            {"$unwind": "$line_items"},
            {"$group": {
                "_id": {"po_id": "$po_id", "line_idx": "$line_items.line_idx"},
                "total_qty": {"$sum": {"$ifNull": ["$line_items.qty", "$line_items.quantity"]}}
            }}
        ]
        line_shipped_agg: Dict[str, Dict[int, int]] = {}
        async for doc in db.sales_deliveries.aggregate(pipe):
            pid = doc["_id"]["po_id"]
            l_idx = int(doc["_id"]["line_idx"])
            if pid not in line_shipped_agg:
                line_shipped_agg[pid] = {}
            line_shipped_agg[pid][l_idx] = int(doc["total_qty"] or 0)

        for pid, ord_doc in orders_map.items():
            raw_lines = ord_doc.get("line_items") or []
            tot_ord = sum(int(l.get("quantity") or 0) for l in raw_lines)
            shipped_map = line_shipped_agg.get(pid, {})
            tot_ship = sum(shipped_map.values())
            all_fulfilled = len(raw_lines) > 0 and all(
                shipped_map.get(idx, 0) >= int(l.get("quantity") or 0)
                for idx, l in enumerate(raw_lines)
            )
            if all_fulfilled or (tot_ord > 0 and tot_ship >= tot_ord):
                po_statuses[pid] = "SELESAI"
            else:
                po_statuses[pid] = "SEBAGIAN"

    enriched_deliveries = []
    filter_norm = (status or "").strip().upper()
    for d in deliveries:
        derived = po_statuses.get(d.get("po_id")) or d.get("status") or "SELESAI"
        if derived not in ("SELESAI", "SEBAGIAN"):
            derived = "SELESAI" if derived == "Terkirim" else "SEBAGIAN"
        d["status"] = derived

        if filter_norm and filter_norm not in ("ALL", "SEMUA", "SEMUA STATUS"):
            if derived != filter_norm:
                continue
        enriched_deliveries.append(d)

    return enriched_deliveries


async def get_sales_delivery_detail(db, delivery_id: str) -> Dict[str, Any]:
    """Get single sales delivery with its PO snapshot and all related shipments."""
    did = str(delivery_id or "").strip()
    delivery = await db.sales_deliveries.find_one({"id": did}, {"_id": 0})
    if not delivery:
        delivery = await db.sales_deliveries.find_one({"sj_number": did}, {"_id": 0})
    if not delivery:
        raise HTTPException(404, f"Pengiriman Pesanan '{delivery_id}' tidak ditemukan.")

    po_id = delivery.get("po_id")
    po_summary = await get_po_delivery_summary(db, po_id)
    delivery["status"] = po_summary.get("delivery_status") or "SELESAI"

    # All deliveries for this PO to display complete multi-shipment history
    all_deliveries = await db.sales_deliveries.find(
        {"po_id": po_id, "is_cancelled": {"$ne": True}},
        {"_id": 0}
    ).sort("shipment_index", 1).to_list(100)

    return {
        "delivery": delivery,
        "po_summary": po_summary,
        "all_po_deliveries": all_deliveries,
    }


def generate_sales_delivery_pdf_bytes(
    delivery: dict,
    po_summary: Optional[dict] = None,
    *,
    recipient_signature_png: Optional[bytes] = None,
    recipient_name: Optional[str] = None,
    recipient_signed_at: Optional[str] = None,
    driver_signature_png: Optional[bytes] = None,
    driver_name: Optional[str] = None,
    driver_signed_at: Optional[str] = None,
) -> bytes:
    """Generate official A4 / Letter PDF for Sales Delivery / Surat Jalan.

    Optional signature PNGs enrich 'Diterima Oleh' and 'Pengirim / Supir' only.
    This is document rendering — it MUST NOT mutate packed/loading/delivery qty.
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable, Image,
    )
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.lib.utils import ImageReader
    from services.delivery_pod_signature import normalize_signature_image_for_pdf

    _sig_tmp_paths: List[str] = []

    def _normalize_sig_file(raw_png: Optional[bytes]):
        """Write normalized PNG to temp; return (path, iw, ih) or None."""
        if not raw_png:
            return None
        normalized = normalize_signature_image_for_pdf(raw_png)
        if not normalized:
            logger.warning("signature image normalize failed — PDF will omit image")
            return None
        import os
        import tempfile
        tmp_path = None
        try:
            fd, tmp_path = tempfile.mkstemp(suffix=".png")
            with os.fdopen(fd, "wb") as fh:
                fh.write(normalized)
            try:
                from PIL import Image as PILImage
                with PILImage.open(tmp_path) as pil:
                    iw, ih = pil.size
            except Exception:
                iw, ih = 300, 300
            _sig_tmp_paths.append(tmp_path)
            return tmp_path, max(iw, 1), max(ih, 1)
        except Exception:
            logger.warning("signature Image embed failed", exc_info=True)
            if tmp_path:
                try:
                    import os as _os
                    _os.unlink(tmp_path)
                except Exception:
                    pass
            return None

    def _sig_image_in_box(tmp_path: str, iw: int, ih: int, max_w: float, max_h: float):
        """Fit stroke into max_w×max_h; preserve aspect — no stretch, no border."""
        scale = min(max_w / iw, max_h / ih)
        draw_w = max(iw * scale, 0.5 * cm)
        draw_h = max(ih * scale, 0.5 * cm)
        img = Image(tmp_path, width=draw_w, height=draw_h)
        img.hAlign = "CENTER"
        return img, draw_w, draw_h

    def _signed_at_label(raw_ts: Optional[str]) -> str:
        if not raw_ts:
            return ""
        try:
            from services.pdf_date import format_pdf_date
            return f"<br/><font size='7' color='#64748b'>Ditandatangani: {format_pdf_date(raw_ts)}</font>"
        except Exception:
            return f"<br/><font size='7' color='#64748b'>Ditandatangani: {raw_ts}</font>"

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.5 * cm,
        leftMargin=1.5 * cm,
        topMargin=1.5 * cm,
        bottomMargin=1.5 * cm,
    )

    styles = getSampleStyleSheet()
    
    company_title_style = ParagraphStyle(
        'CompanyTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=14,
        leading=17,
        textColor=colors.HexColor('#0f172a'),
    )
    
    doc_title_style = ParagraphStyle(
        'DocTitle',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=13,
        leading=16,
        alignment=2, # Right
        textColor=colors.HexColor('#0369a1'),
    )

    meta_label_style = ParagraphStyle(
        'MetaLabel',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#334155'),
    )
    
    meta_val_style = ParagraphStyle(
        'MetaVal',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#0f172a'),
    )

    tbl_header_style = ParagraphStyle(
        'TblHeader',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#1e293b'),
    )

    tbl_cell_style = ParagraphStyle(
        'TblCell',
        parent=styles['Normal'],
        fontName='Helvetica',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#0f172a'),
    )
    sig_center_style = ParagraphStyle(
        'SigCenter',
        parent=tbl_cell_style,
        alignment=1,  # TA_CENTER
    )
    sig_header_style = ParagraphStyle(
        'SigHeader',
        parent=meta_label_style,
        alignment=1,  # TA_CENTER
    )

    tbl_cell_bold = ParagraphStyle(
        'TblCellBold',
        parent=styles['Normal'],
        fontName='Helvetica-Bold',
        fontSize=8.5,
        leading=11,
        textColor=colors.HexColor('#0f172a'),
    )

    elements = []

    # 1. Header: Company Info + Document Title
    header_data = [
        [
            Paragraph("<b>ETERNAL PACKAGING</b><br/><font color='#64748b' size='7.5'>Food Packaging & Printing Solutions</font>", company_title_style),
            Paragraph("<b>PENGIRIMAN PESANAN / SURAT JALAN</b><br/><font size='9' color='#0284c7'><b>" + str(delivery.get('sj_number') or '') + "</b></font>", doc_title_style),
        ]
    ]
    header_tbl = Table(header_data, colWidths=[9.5 * cm, 8.5 * cm])
    header_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 0),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_tbl)
    elements.append(Spacer(1, 0.2 * cm))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor('#0284c7'), spaceAfter=8, spaceBefore=0))

    # 2. Metadata Grid
    from services.pdf_date import format_pdf_date
    d_date = format_pdf_date(delivery.get("delivery_date"))
    meta_data = [
        [
            Paragraph("<b>Nomor PO</b>", meta_label_style),
            Paragraph(f": {delivery.get('po_number') or '-'}", meta_val_style),
            Paragraph("<b>Customer</b>", meta_label_style),
            Paragraph(f": {delivery.get('customer') or '-'}", meta_val_style),
        ],
        [
            Paragraph("<b>Nomor SJ</b>", meta_label_style),
            Paragraph(f": <b>{delivery.get('sj_number') or '-'}</b> (Kirim #{delivery.get('shipment_index', 1)})", meta_val_style),
            Paragraph("<b>Outlet / Cabang</b>", meta_label_style),
            Paragraph(f": {delivery.get('outlet') or '-'}", meta_val_style),
        ],
        [
            Paragraph("<b>Tanggal Kirim</b>", meta_label_style),
            Paragraph(f": {d_date}", meta_val_style),
            Paragraph("<b>Penerima (PIC)</b>", meta_label_style),
            Paragraph(f": {delivery.get('recipient') or '-'}", meta_val_style),
        ],
        [
            Paragraph("<b>Sales</b>", meta_label_style),
            Paragraph(f": {delivery.get('sales') or '-'}", meta_val_style),
            Paragraph("<b>Alamat Kirim</b>", meta_label_style),
            Paragraph(f": {delivery.get('address') or '-'}", meta_val_style),
        ],
        [
            Paragraph("<b>Ekspedisi</b>", meta_label_style),
            Paragraph(f": {delivery.get('expedition') or '-'}", meta_val_style),
            Paragraph("<b>Zona Kirim</b>", meta_label_style),
            Paragraph(f": {delivery.get('delivery_zone') or '-'}", meta_val_style),
        ],
    ]
    meta_tbl = Table(meta_data, colWidths=[2.5 * cm, 6.5 * cm, 2.5 * cm, 6.5 * cm])
    meta_tbl.setStyle(TableStyle([
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
        ('LEFTPADDING', (0, 0), (-1, -1), 2),
        ('RIGHTPADDING', (0, 0), (-1, -1), 2),
    ]))
    elements.append(meta_tbl)
    elements.append(Spacer(1, 0.4 * cm))

    # 3. Line Items Table
    table_rows = [
        [
            Paragraph("<b>No</b>", tbl_header_style),
            Paragraph("<b>Barang / Jasa</b>", tbl_header_style),
            Paragraph("<b>Kode</b>", tbl_header_style),
            Paragraph("<b>Proses</b>", tbl_header_style),
            Paragraph("<b>Qty Kirim</b>", tbl_header_style),
            Paragraph("<b>Satuan</b>", tbl_header_style),
            Paragraph("<b>Catatan</b>", tbl_header_style),
        ]
    ]

    total_shipped = 0
    line_items = delivery.get("line_items") or []
    for idx, li in enumerate(line_items):
        q = int(li.get("qty") if li.get("qty") is not None else li.get("quantity", 0))
        total_shipped += q
        proc = "Cetak" if (li.get("production_process") or "").lower() != "non_print" else "Non-Cetak"
        table_rows.append([
            Paragraph(str(idx + 1), tbl_cell_style),
            Paragraph(f"<b>{li.get('name') or '-'}</b>" + (f"<br/><font size='7' color='#64748b'>{li.get('color')}</font>" if li.get('color') else ""), tbl_cell_style),
            Paragraph(str(li.get("item_code") or "-"), tbl_cell_style),
            Paragraph(proc, tbl_cell_style),
            Paragraph(f"<b>{q:,}</b>", tbl_cell_bold),
            Paragraph(str(li.get("unit") or "pcs"), tbl_cell_style),
            Paragraph(str(li.get("notes") or "-"), tbl_cell_style),
        ])

    # Summary row
    table_rows.append([
        Paragraph("", tbl_cell_style),
        Paragraph("<b>TOTAL KUANTITAS PENGIRIMAN</b>", tbl_cell_bold),
        Paragraph("", tbl_cell_style),
        Paragraph("", tbl_cell_style),
        Paragraph(f"<b>{total_shipped:,}</b>", tbl_cell_bold),
        Paragraph("pcs", tbl_cell_bold),
        Paragraph("", tbl_cell_style),
    ])

    items_tbl = Table(
        table_rows,
        colWidths=[0.9 * cm, 6.2 * cm, 2.0 * cm, 1.8 * cm, 2.0 * cm, 1.6 * cm, 3.5 * cm]
    )
    items_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#f1f5f9')),
        ('LINEBELOW', (0, 0), (-1, 0), 1.5, colors.HexColor('#cbd5e1')),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('GRID', (0, 0), (-1, -2), 0.5, colors.HexColor('#e2e8f0')),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8fafc')),
        ('LINEABOVE', (0, -1), (-1, -1), 1, colors.HexColor('#94a3b8')),
        ('LINEBELOW', (0, -1), (-1, -1), 1, colors.HexColor('#94a3b8')),
        ('ALIGN', (4, 0), (4, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    elements.append(items_tbl)
    elements.append(Spacer(1, 0.3 * cm))

    # 4. Catatan / Notes if present
    notes = (delivery.get("notes") or "").strip()
    if notes:
        notes_tbl = Table([
            [Paragraph("<b>Catatan Pengiriman:</b>", meta_label_style)],
            [Paragraph(notes, meta_val_style)]
        ], colWidths=[18 * cm])
        notes_tbl.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, -1), colors.HexColor('#f8fafc')),
            ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#e2e8f0')),
            ('PADDING', (0, 0), (-1, -1), 6),
        ]))
        elements.append(notes_tbl)
        elements.append(Spacer(1, 0.4 * cm))

    # 5. Signatures Block
    display_recipient = (recipient_name or delivery.get("recipient") or "").strip()
    display_driver = (driver_name or delivery.get("driver_name") or "").strip()
    recipient_signed_label = _signed_at_label(recipient_signed_at)
    driver_signed_label = _signed_at_label(driver_signed_at)

    def _sig_name_block(name: str, sublabel: str, signed_label: str) -> Paragraph:
        name_hint = f"<b>{name}</b>" if name else "_____________________ "
        return Paragraph(
            f"( {name_hint} )<br/>"
            f"<font size='7' color='#64748b'>{sublabel}</font>{signed_label}",
            sig_center_style,
        )

    # Fixed-height signature slots so recipient/driver names share one baseline.
    # Shared render box = recipient's current fit size (never enlarge recipient);
    # driver is contained in that same box (aspect preserved).
    _sig_col_w = 5.6 * cm
    _sig_cap_w = 5.2 * cm
    _sig_cap_h = 4.0 * cm

    recip_file = _normalize_sig_file(recipient_signature_png)
    driver_file = _normalize_sig_file(driver_signature_png)

    if recip_file:
        _rp, _riw, _rih = recip_file
        _rs = min(_sig_cap_w / _riw, _sig_cap_h / _rih)
        box_w = max(_riw * _rs, 0.5 * cm)
        box_h = max(_rih * _rs, 0.5 * cm)
    elif driver_file:
        _dp, _diw, _dih = driver_file
        _ds = min(_sig_cap_w / _diw, _sig_cap_h / _dih)
        box_w = max(_diw * _ds, 0.5 * cm)
        box_h = max(_dih * _ds, 0.5 * cm)
    else:
        box_w, box_h = _sig_cap_w, _sig_cap_h

    _sig_slot_h = box_h

    recipient_img = None
    driver_img = None
    if recip_file:
        recipient_img, _, _ = _sig_image_in_box(*recip_file, box_w, box_h)
    if driver_file:
        driver_img, _, _ = _sig_image_in_box(*driver_file, box_w, box_h)

    def _sig_slot(img):
        """Borderless equal-height slot; stroke bottom-aligned above the name."""
        cell = img if img is not None else Spacer(1, 0.1 * cm)
        slot = Table([[cell]], colWidths=[_sig_col_w], rowHeights=[_sig_slot_h])
        slot.setStyle(TableStyle([
            ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
            ('VALIGN', (0, 0), (-1, -1), 'BOTTOM'),
            ('LEFTPADDING', (0, 0), (-1, -1), 0),
            ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ('TOPPADDING', (0, 0), (-1, -1), 0),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
        ]))
        return slot

    def _sig_column_cell(img, name: str, sublabel: str, signed_label: str):
        # Nested Table (not KeepTogether) — KeepTogether+Image inside Table cells
        # can explode row height on some ReportLab versions.
        if img is not None:
            inner = Table(
                [[_sig_slot(img)], [Spacer(1, 0.12 * cm)], [_sig_name_block(name, sublabel, signed_label)]],
                colWidths=[_sig_col_w],
            )
            inner.setStyle(TableStyle([
                ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
                ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                ('TOPPADDING', (0, 0), (-1, -1), 1),
                ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
                ('LEFTPADDING', (0, 0), (-1, -1), 0),
                ('RIGHTPADDING', (0, 0), (-1, -1), 0),
            ]))
            return inner
        name_hint = f"<b>{name}</b>" if name else "_____________________ "
        return Paragraph(
            f"<br/><br/><br/>( {name_hint} )<br/>"
            f"<font size='7' color='#64748b'>{sublabel}</font>{signed_label}",
            sig_center_style,
        )

    recipient_cell = _sig_column_cell(
        recipient_img,
        display_recipient,
        "Tanda Tangan & Nama Terang",
        recipient_signed_label,
    )
    driver_cell = _sig_column_cell(
        driver_img,
        display_driver,
        "Logistik / Ekspedisi",
        driver_signed_label,
    )
    hormat_name = Paragraph(
        f"( <b>{delivery.get('created_by_name') or 'Eternal Packaging'}</b> )"
        f"<br/><font size='7' color='#64748b'>Bagian Penjualan</font>",
        sig_center_style,
    )
    # Same signature-slot height so Hormat Kami name stays on the shared baseline.
    hormat_cell = Table(
        [[_sig_slot(None)], [Spacer(1, 0.12 * cm)], [hormat_name]],
        colWidths=[_sig_col_w],
    )
    hormat_cell.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 1),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 1),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
    ]))

    sig_data = [
        [
            Paragraph("Diterima Oleh,", sig_header_style),
            Paragraph("Pengirim / Supir,", sig_header_style),
            Paragraph("Hormat Kami,", sig_header_style),
        ],
        [
            recipient_cell,
            driver_cell,
            hormat_cell,
        ],
    ]
    sig_tbl = Table(sig_data, colWidths=[6.0 * cm, 6.0 * cm, 6.0 * cm])
    sig_tbl.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(sig_tbl)

    try:
        doc.build(elements)
        pdf_bytes = buffer.getvalue()
    finally:
        import os
        for tmp in _sig_tmp_paths:
            try:
                os.unlink(tmp)
            except Exception:
                pass
        buffer.close()
    return pdf_bytes


async def generate_signed_sj_pdf_for_order(
    db,
    order: dict,
    sig: dict,
    obj_storage,
    *,
    user: Optional[dict] = None,
    driver_sig: Optional[dict] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Render signed SJ PDF for exact delivery_note/shipment. Pure document write to storage.

    Returns (signed_storage_path, error_code). Does not mutate packed/loading/delivered qty
    or workflow stage. Preserves original SJ storage_path on the file entry.
    """
    from services.surat_jalan_files import find_file, find_generated_sj_file
    from services.delivery_pod_signature import (
        get_active_driver_signature,
        get_frozen_driver_signatures,
        resolve_pdf_driver_display_name,
        storage_key_for_signed_sj,
        now_iso as _now,
    )

    sj_file_id = str(sig.get("sj_file_id") or sig.get("delivery_note_id") or "").strip()
    shipment_id = str(sig.get("shipment_id") or "").strip()
    if not sj_file_id and shipment_id:
        gen = find_generated_sj_file(order, shipment_id)
        if gen:
            sj_file_id = str(gen.get("id") or "")
    entry = find_file(order, sj_file_id) if sj_file_id else None
    if not entry and shipment_id:
        entry = find_generated_sj_file(order, shipment_id)
        if entry:
            sj_file_id = str(entry.get("id") or "")

    if driver_sig is None:
        driver_sig = get_active_driver_signature(order)
        if not driver_sig:
            # Prefer frozen driver sig matching confirmation / shipment
            conf_id = str(sig.get("confirmation_id") or "")
            for ds in get_frozen_driver_signatures(order):
                if conf_id and str(ds.get("confirmation_id") or "") == conf_id:
                    driver_sig = ds
                    break
                if shipment_id and str(ds.get("shipment_id") or "") == shipment_id:
                    driver_sig = ds
                    break
            if not driver_sig:
                frozen = get_frozen_driver_signatures(order)
                driver_sig = frozen[-1] if frozen else None

    delivery = None
    if shipment_id:
        delivery = await db.sales_deliveries.find_one(
            {"id": shipment_id, "is_cancelled": {"$ne": True}},
            {"_id": 0},
        )
    if not delivery and sj_file_id:
        delivery = await db.sales_deliveries.find_one(
            {"generated_sj_file_id": sj_file_id, "is_cancelled": {"$ne": True}},
            {"_id": 0},
        )
    if not delivery:
        lines = (entry or {}).get("lines") or []
        delivery = {
            "id": shipment_id or sj_file_id or str(uuid.uuid4()),
            "po_id": order.get("id"),
            "po_number": order.get("po_number"),
            "sj_number": (entry or {}).get("sj_number") or order.get("delivery_note_number"),
            "customer": order.get("customer") or order.get("customer_name"),
            "outlet": order.get("outlet"),
            "address": order.get("address") or order.get("delivery_address"),
            "recipient": sig.get("recipient_name") or order.get("recipient") or order.get("recipient_name"),
            "driver_name": resolve_pdf_driver_display_name(order, driver_sig=driver_sig, user=user),
            "sales": order.get("sales") or order.get("sales_name"),
            "delivery_zone": order.get("delivery_zone") or order.get("zone"),
            "expedition": order.get("expedition"),
            "delivery_date": order.get("delivery_note_created_at") or order.get("deadline"),
            "shipment_index": 1,
            "line_items": [
                {
                    "name": ln.get("name"),
                    "item_code": ln.get("item_code"),
                    "production_process": ln.get("production_process"),
                    "qty": ln.get("qty") if ln.get("qty") is not None else ln.get("quantity"),
                    "unit": ln.get("unit") or "pcs",
                    "notes": ln.get("notes"),
                    "color": ln.get("color"),
                    "line_idx": ln.get("line_idx"),
                    "line_id": ln.get("line_id"),
                }
                for ln in lines
            ] or [
                {
                    "name": li.get("name"),
                    "item_code": li.get("item_code") or li.get("accurate_item_id"),
                    "production_process": li.get("production_process"),
                    "qty": li.get("quantity"),
                    "unit": li.get("unit") or "pcs",
                    "color": li.get("color"),
                    "line_idx": idx,
                    "line_id": li.get("line_id") or li.get("id"),
                }
                for idx, li in enumerate(order.get("line_items") or [])
            ],
            "notes": order.get("delivery_note_notes"),
            "created_by_name": (user or {}).get("name") or order.get("delivery_note_created_by_name"),
        }
    else:
        delivery = dict(delivery)
        delivery["driver_name"] = resolve_pdf_driver_display_name(
            order, driver_sig=driver_sig, delivery=delivery, user=user
        )

    sig_path = str(sig.get("signature_storage_path") or "").strip()
    png_bytes = None
    if sig_path and obj_storage:
        try:
            png_bytes, _ct = obj_storage.get_object(sig_path)
        except Exception:
            png_bytes = None
    if not png_bytes:
        return None, "signature_bytes_missing"

    driver_png = None
    driver_path = ""
    if driver_sig:
        driver_path = str(
            driver_sig.get("driver_signature_storage_path")
            or driver_sig.get("signature_storage_path")
            or ""
        ).strip()
    if driver_path and obj_storage:
        try:
            driver_png, _ct = obj_storage.get_object(driver_path)
        except Exception:
            driver_png = None
    if not driver_png:
        return None, "driver_signature_bytes_missing"

    try:
        pdf_bytes = generate_sales_delivery_pdf_bytes(
            delivery,
            None,
            recipient_signature_png=png_bytes,
            recipient_name=sig.get("recipient_name"),
            recipient_signed_at=sig.get("recipient_signed_at"),
            driver_signature_png=driver_png,
            driver_name=resolve_pdf_driver_display_name(
                order, driver_sig=driver_sig, delivery=delivery, user=user
            ),
            driver_signed_at=(driver_sig or {}).get("driver_signed_at"),
        )
    except Exception:
        logger.exception("signed SJ PDF generate failed")
        return None, "pdf_generate_failed"

    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF"):
        return None, "pdf_invalid"

    po_id = str(order.get("id") or "")
    signed_path = storage_key_for_signed_sj(po_id, sj_file_id or shipment_id or sig.get("id") or "sig")
    try:
        stored = obj_storage.put_object(signed_path, pdf_bytes, "application/pdf")
        signed_path = stored.get("path") or signed_path
    except Exception:
        logger.exception("signed SJ storage write failed")
        return None, "storage_failed"

    stamp = _now()
    if sj_file_id:
        await db.orders.update_one(
            {"id": po_id, "surat_jalan_files.id": sj_file_id},
            {"$set": {
                "surat_jalan_files.$.signed_storage_path": signed_path,
                "surat_jalan_files.$.signed_at": stamp,
                "surat_jalan_files.$.signed_recipient_name": sig.get("recipient_name"),
                "surat_jalan_files.$.signed_driver_name": resolve_pdf_driver_display_name(
                    order, driver_sig=driver_sig, delivery=delivery, user=user
                ),
                "surat_jalan_files.$.original_storage_path": (entry or {}).get("original_storage_path")
                    or (entry or {}).get("storage_path"),
            }},
        )
        await db.files.update_one(
            {"id": sj_file_id, "kind": "surat_jalan"},
            {"$set": {
                "signed_storage_path": signed_path,
                "signed_at": stamp,
                "original_storage_path": (entry or {}).get("original_storage_path")
                    or (entry or {}).get("storage_path"),
            }},
            upsert=False,
        )
    return signed_path, None
