"""Sales Delivery (Pengiriman Pesanan) Service.

Manages backend-authoritative sales delivery notes (Surat Jalan Penjualan)
linked stably to Sales Orders (Pesanan Penjualan / PO).
Supports partial shipments, multi-line integrity, concurrency/duplicate safety,
and PDF document rendering.
"""
from __future__ import annotations

import asyncio
import io
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

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
        return "SJ-" + datetime.now(timezone.utc).strftime("%Y%m%d-001")
    
    # 1. Match PO/2026/08/30/000123 or SO/2026/08/30/000123 -> SJ/2026/08/30/000123
    # 2. Match PO-20260830-001 or SO-20260830-001 -> SJ-20260830-001
    # 3. Match PO.2026.08.001 -> SJ.2026.08.001
    # 4. Match PO 12345 -> SJ 12345
    # 5. Match PO12345 -> SJ12345
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
    
    Index 1: base SJ (e.g. SJ/2026/08/30/000123 or SJ-20260830-001)
    Index 2: base SJ + "-2"
    Index 3: base SJ + "-3"
    """
    base = derive_base_sj_number(po_number)
    if shipment_index <= 1:
        return base
    return f"{base}-{shipment_index}"


async def get_po_delivery_summary(db, po_id_or_doc: Any) -> Dict[str, Any]:
    """Calculate delivered & remaining quantities per line item for a PO."""
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

    if is_cancelled:
        delivery_status = "DIBATALKAN"
    elif total_shipped == 0:
        delivery_status = "BELUM DIKIRIM"
    elif total_remaining > 0:
        delivery_status = "SEBAGIAN DIKIRIM"
    else:
        delivery_status = "SELESAI PENGIRIMAN"

    next_shipment_index = len(existing_deliveries) + 1
    suggested_sj = derive_sj_number(po_number, next_shipment_index)

    return {
        "po_id": po_id,
        "po_number": po_number,
        "order_date": order.get("order_date"),
        "customer": order.get("customer") or "",
        "outlet": order.get("outlet") or "",
        "recipient": order.get("recipient") or "",
        "address": order.get("address") or "",
        "delivery_zone": order.get("delivery_zone") or "",
        "sales": order.get("sales") or order.get("seller") or "",
        "expedition": order.get("expedition") or "Internal fleet",
        "notes": order.get("notes") or "",
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
        if not summary["can_create_delivery"]:
            raise HTTPException(400, "Seluruh kuantitas Pesanan Penjualan ini sudah selesai dibuatkan Pengiriman Pesanan.")

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

            try:
                qty_to_ship = int(item.get("qty") if item.get("qty") is not None else item.get("quantity", 0))
            except (ValueError, TypeError):
                qty_to_ship = 0

            if qty_to_ship <= 0:
                continue

            remain = target_line["remaining_qty"]
            if qty_to_ship > remain:
                raise HTTPException(
                    400,
                    f"Kuantitas pengiriman untuk '{target_line['name']}' ({qty_to_ship} {target_line['unit']}) "
                    f"melebihi sisa PO yang tersedia ({remain} {target_line['unit']})."
                )

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

        # 4. Determine shipment index and SJ number backend-authoritatively
        now = now_iso()
        delivery_date = str(payload.get("delivery_date") or now[:10]).strip()
        po_number = order.get("po_number")

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
                "customer": summary["customer"],
                "outlet": (payload.get("outlet") or summary["outlet"] or "").strip(),
                "recipient": (payload.get("recipient") or summary["recipient"] or "").strip(),
                "address": (payload.get("address") or summary["address"] or "").strip(),
                "delivery_zone": (payload.get("delivery_zone") or summary["delivery_zone"] or "").strip(),
                "sales": (payload.get("sales") or summary["sales"] or "").strip(),
                "expedition": (payload.get("expedition") or summary["expedition"] or "").strip(),
                "notes": (payload.get("notes") or "").strip(),
                "created_by": user.get("email") or "",
                "created_by_name": user.get("name") or user.get("email") or "",
                "created_by_role": user.get("role") or "",
                "created_at": now,
                "updated_at": now,
                "line_items": validated_lines,
                "total_qty": total_delivery_qty,
                "item_count": len(validated_lines),
                "status": "Terkirim" if (summary["total_remaining"] - total_delivery_qty <= 0 and shipment_index == 1) else "Dikirim",
                "is_cancelled": False,
            }

            try:
                await db.sales_deliveries.insert_one(delivery_doc)
                delivery_doc.pop("_id", None)
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

    if status and status.strip() and status.lower() != "all":
        query["status"] = {"$regex": f"^{re.escape(status.strip())}$", "$options": "i"}

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
    return deliveries


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


def generate_sales_delivery_pdf_bytes(delivery: dict, po_summary: Optional[dict] = None) -> bytes:
    """Generate official A4 / Letter PDF for Sales Delivery / Surat Jalan."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether, HRFlowable
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm

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
    d_date = str(delivery.get("delivery_date") or "")[:10]
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
    sig_data = [
        [
            Paragraph("Diterima Oleh,", meta_label_style),
            Paragraph("Pengirim / Supir,", meta_label_style),
            Paragraph("Hormat Kami,", meta_label_style),
        ],
        [
            Paragraph("<br/><br/><br/>(_____________________)<br/><font size='7' color='#64748b'>Tanda Tangan & Nama Terang</font>", tbl_cell_style),
            Paragraph("<br/><br/><br/>(_____________________)<br/><font size='7' color='#64748b'>Logistik / Ekspedisi</font>", tbl_cell_style),
            Paragraph(f"<br/><br/><br/>( <b>{delivery.get('created_by_name') or 'Eternal Packaging'}</b> )<br/><font size='7' color='#64748b'>Bagian Penjualan</font>", tbl_cell_style),
        ]
    ]
    sig_tbl = Table(sig_data, colWidths=[6.0 * cm, 6.0 * cm, 6.0 * cm])
    sig_tbl.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]))
    elements.append(KeepTogether([sig_tbl]))

    doc.build(elements)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes
