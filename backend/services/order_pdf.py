"""Eternal Pesanan Penjualan PDF. Read-only rendering of an orders document."""
from __future__ import annotations

from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.platypus import Image, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.units import mm

from services.pdf_date import format_pdf_date
from services.pdf_layout import (
    build_pdf,
    content_width,
    esc,
    format_qty,
    logo_path,
    scale_widths,
    styles,
)


def _text(value) -> str:
    return str(value or "").strip()


def _delivery_date(order: dict) -> str:
    for key in (
        "tanggal_pengiriman",
        "effective_delivery_date",
        "delivery_deadline_effective",
        "scheduled_delivery_date",
        "estimated_delivery",
        "delivery_deadline_manual",
    ):
        raw = order.get(key)
        if _text(raw):
            return format_pdf_date(raw, fallback=_text(raw))
    return ""


def _operators_for_line(order: dict, line: dict, index: int) -> str:
    splits = [row for row in (order.get("operator_splits") or []) if isinstance(row, dict)]
    try:
        line_idx = int(line.get("line_idx") if line.get("line_idx") is not None else index)
    except (TypeError, ValueError):
        line_idx = index
    indexed: List[dict] = []
    unindexed: List[dict] = []
    for split in splits:
        raw_idx = split.get("line_idx")
        if raw_idx is None or str(raw_idx).strip() == "":
            unindexed.append(split)
            continue
        try:
            if int(raw_idx) == line_idx:
                indexed.append(split)
        except (TypeError, ValueError):
            unindexed.append(split)
    chosen = indexed
    lines = [row for row in (order.get("line_items") or []) if isinstance(row, dict)]
    if not chosen and unindexed and len(lines) == 1:
        chosen = unindexed
    names: List[str] = []
    seen = set()
    for split in chosen:
        name = _text(split.get("operator_name"))
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return ", ".join(names)


def _keterangan(order: dict) -> str:
    parts: List[str] = []
    header = _text(order.get("notes"))
    if header:
        parts.append(header)
    for line in order.get("line_items") or []:
        if not isinstance(line, dict):
            continue
        note = _text(line.get("notes"))
        if note and note not in parts:
            parts.append(note)
    return "\n".join(parts)


def _meta_cell(label: str, value: str, sty) -> Paragraph:
    shown = esc(value) if value else "&nbsp;"
    return Paragraph(f"{esc(label)}<br/><b>{shown}</b>", sty["small"])


def _box(flowable, width: float):
    table = Table([[flowable]], colWidths=[width])
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def generate_sales_order_pdf_bytes(order: dict) -> bytes:
    """A4 landscape Pesanan Penjualan. Same content, wider page."""
    page = landscape(A4)
    sty = styles()
    width = content_width(page)
    elements = []

    path = logo_path()
    if path:
        img = Image(path, width=52 * mm, height=17.6 * mm)
        img.hAlign = "LEFT"
        elements.append(img)
    else:
        elements.append(Paragraph("<b>ETERNAL</b>", sty["company"]))
    elements.append(Spacer(1, 4 * mm))

    address = _text(order.get("address"))
    customer = Paragraph(
        f"Kepada<br/><b>{esc(_text(order.get('customer')) or '-')}</b><br/>{esc(address)}",
        sty["body"],
    )
    meta = [
        [
            _meta_cell("Tanggal", format_pdf_date(order.get("order_date") or order.get("created_at")), sty),
            _meta_cell("Nomor", _text(order.get("po_number")), sty),
        ],
        [
            _meta_cell("Syarat Pembayaran", _text(order.get("payment_terms")), sty),
            _meta_cell("User", _text(order.get("created_by_user")), sty),
        ],
        [
            _meta_cell("Ekspedisi", _text(order.get("expedition")), sty),
            _meta_cell("Estimasi Tanggal Pengiriman", _delivery_date(order), sty),
        ],
        [
            _meta_cell("Zona Pengiriman", _text(order.get("delivery_zone")), sty),
            _meta_cell("Penjual", _text(order.get("sales")), sty),
        ],
    ]
    grid_width = width * 0.56
    meta_table = Table(meta, colWidths=[grid_width / 2, grid_width / 2])
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.7, colors.black),
        ("LINEAFTER", (0, 0), (0, -1), 0.6, colors.black),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.black, None, (2, 2)),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    left_width = width - grid_width
    customer_cell = Table([[customer]], colWidths=[left_width - 3 * mm])
    customer_cell.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, -1), 0.7, colors.black),
        ("LEFTPADDING", (0, 0), (-1, -1), 1),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    header = Table(
        [[customer_cell, meta_table]],
        colWidths=[left_width, grid_width],
    )
    header.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(header)
    elements.append(Spacer(1, 4 * mm))

    header_row = [
        Paragraph("<b>Kode Barang</b>", sty["header"]),
        Paragraph("<b>Nama Barang</b>", sty["header"]),
        Paragraph("<b>Kts.</b>", sty["header"]),
        Paragraph("<b>Satuan</b>", sty["header"]),
        Paragraph("<b>Operator</b>", sty["header"]),
        Paragraph("<b>Warna</b>", sty["header"]),
    ]
    rows = [header_row]
    total_qty = 0
    lines = [row for row in (order.get("line_items") or []) if isinstance(row, dict)]
    for index, line in enumerate(lines):
        try:
            qty = int(float(line.get("quantity") or 0))
        except (TypeError, ValueError):
            qty = 0
        total_qty += qty
        rows.append([
            Paragraph(esc(_text(line.get("item_code"))), sty["body"]),
            Paragraph(esc(_text(line.get("name") or line.get("item_name"))), sty["body"]),
            Paragraph(esc(format_qty(qty)), sty["right"]),
            Paragraph(esc(_text(line.get("unit")) or "pcs"), sty["right"]),
            Paragraph(esc(_operators_for_line(order, line, index)), sty["body"]),
            Paragraph(esc(_text(line.get("color"))), sty["right"]),
        ])
    if len(rows) == 1:
        rows.append([Paragraph("", sty["body"]) for _ in range(6)])
    col_widths = scale_widths([78, 168, 52, 44, 120, 71], width)
    items = Table(rows, colWidths=col_widths, repeatRows=1)
    items.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.black),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(items)
    elements.append(Spacer(1, 4 * mm))

    note_text = _keterangan(order) or " "
    note = Paragraph(f"<b>Keterangan :</b><br/>{esc(note_text)}", sty["body"])
    if order.get("total_quantity") is not None:
        try:
            shown_total = int(float(order.get("total_quantity")))
        except (TypeError, ValueError):
            shown_total = total_qty
    else:
        shown_total = total_qty
    totals = [
        [
            Paragraph("<b>Total Kuantitas</b>", sty["body"]),
            Paragraph(f"<b>{esc(format_qty(shown_total))}</b>", sty["right_bold"]),
        ],
        [
            Paragraph("<b>Jenis Barang</b>", sty["body"]),
            Paragraph(f"<b>{len(lines)}</b>", sty["right_bold"]),
        ],
    ]
    totals_table = Table(totals, colWidths=[38 * mm, 22 * mm])
    totals_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.6, colors.black),
        ("BACKGROUND", (0, 0), (0, -1), colors.white),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    note_width = width - 64 * mm
    bottom = Table(
        [[_box(note, note_width), totals_table]],
        colWidths=[note_width + 2 * mm, 62 * mm],
    )
    bottom.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("ALIGN", (1, 0), (1, 0), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(bottom)
    elements.append(Spacer(1, 8 * mm))

    labels = ["Operator", "Pengiriman", "Penerima"]
    sig_cells = []
    line_cells = []
    name_cells = []
    slot = width / 3
    line_width = slot - 18 * mm
    for label in labels:
        sig_cells.append(Paragraph(esc(label), sty["center"]))
        line_cells.append(Spacer(1, 16 * mm))
        line = Table([[Paragraph("Nama", sty["center"])]], colWidths=[line_width])
        line.setStyle(TableStyle([
            ("LINEABOVE", (0, 0), (-1, -1), 0.6, colors.black),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ]))
        name_cells.append(line)
    sig = Table([sig_cells, line_cells, name_cells], colWidths=[slot, slot, slot])
    sig.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "BOTTOM"),
        ("TOPPADDING", (0, 0), (-1, 0), 2),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 1),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(sig)
    return build_pdf(elements, page)
