"""Controlled invoice/proforma footer renderer for Design Cetakan Phase 2.

Groups visible elements into left / center / right columns by align.
Signature and estimasi blocks reuse existing design assets — no DB I/O.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from services.pdf_layout import esc, logo_flowable, signature_image_flowable
from services.print_context_resolver import resolve_source_value
from services.print_field_registry import (
    ALIGN_CENTER,
    ALIGN_LEFT,
    ALIGN_RIGHT,
    ELEMENT_TYPE_SIGNATURE,
    ELEMENT_TYPE_STATIC,
    SOURCE_COMPANY_ADDRESS,
    SOURCE_COMPANY_LOGO,
    SOURCE_COMPANY_NAME,
    SOURCE_ESTIMASI_BLOCK,
    SOURCE_PO_SIGNATURES_BLOCK,
    SOURCE_SIGNATURE_PREPARED,
    SOURCE_STATIC_TEXT,
    VALUE_TYPE_ESTIMASI,
    VALUE_TYPE_IMAGE,
    VALUE_TYPE_MULTILINE,
    VALUE_TYPE_PO_SIGNATURES,
    VALUE_TYPE_SIGNATURE,
    field_spec,
)


def footer_elements_active(elements: Any) -> bool:
    """True when a non-empty sanitized element list should drive the footer."""
    return isinstance(elements, list) and len(elements) > 0


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    return bool(value)


def _align(value: Any) -> str:
    raw = str(value or ALIGN_LEFT).strip().lower()
    if raw in {ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT}:
        return raw
    return ALIGN_LEFT


def _visible_elements(elements: Sequence[dict]) -> List[dict]:
    out: List[dict] = []
    for row in elements:
        if not isinstance(row, dict):
            continue
        if not _bool(row.get("visible"), True):
            continue
        out.append(row)
    return out


def _group_by_align(elements: Sequence[dict]) -> Dict[str, List[dict]]:
    groups = {ALIGN_LEFT: [], ALIGN_CENTER: [], ALIGN_RIGHT: []}
    for row in elements:
        groups[_align(row.get("align"))].append(row)
    return groups


def _para(text: str, sty: dict, *, bold: bool = False):
    body = esc(text) if text else "&nbsp;"
    if bold and text:
        body = f"<b>{body}</b>"
    return Paragraph(body, sty["small"])


def _multiline_html(text: str) -> str:
    lines = [esc(part) for part in str(text or "").split("\n") if str(part).strip()]
    return "<br/>".join(lines) if lines else "&nbsp;"


def _estimasi_box(block: Any, sty: dict, col_width: float) -> Table:
    title = ""
    lines: List[str] = []
    if isinstance(block, dict):
        title = str(block.get("title") or "").strip()
        raw_lines = block.get("lines") or []
        if isinstance(raw_lines, (list, tuple)):
            lines = [str(line).strip() for line in raw_lines if str(line).strip()]
    elif isinstance(block, str):
        title = block.strip()
    html = "<b>" + esc(title) + "</b><br/>" + "<br/>".join(esc(line) for line in lines)
    para = Paragraph(html if (title or lines) else "&nbsp;", sty["red_small"])
    table = Table([[para]], colWidths=[col_width])
    accent = sty.get("accent")
    table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 1.2, accent),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return table


def _signature_flowable(
    *,
    title: str,
    footer: str,
    middle: str,
    image_bytes: Optional[bytes],
    sty: dict,
    col_width: float,
) -> Table:
    sig_img = signature_image_flowable(
        image_bytes,
        max_width=max(col_width - 4 * mm, 10 * mm),
        max_height=14 * mm,
    )
    middle_cell = sig_img if sig_img is not None else Paragraph(
        f"<b>{esc(middle)}</b>" if middle else "&nbsp;",
        sty["center"],
    )
    mid_top = 2 * mm if sig_img is not None else 10 * mm
    column = Table(
        [
            [Paragraph(esc(title), sty["center"])],
            [middle_cell],
            [Paragraph(esc(footer), sty["center"])],
        ],
        colWidths=[col_width],
    )
    column.setStyle(TableStyle([
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 1), (-1, 1), "BOTTOM"),
        ("TOPPADDING", (0, 1), (-1, 1), mid_top),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 3),
        ("LINEABOVE", (0, 2), (-1, 2), 0.6, colors.black),
        ("TOPPADDING", (0, 2), (-1, 2), 2),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
    ]))
    return column


def _po_signatures_block(block: Any, sty: dict, width: float) -> Table:
    data = block if isinstance(block, dict) else {}
    labels = list(data.get("labels") or ["Operator", "Pengiriman", "Penerima"])
    footer_name = str(data.get("footer_name") or "Nama").strip() or "Nama"
    while len(labels) < 3:
        labels.append("")
    labels = labels[:3]
    slot = width / 3
    line_width = max(slot - 18 * mm, 20 * mm)
    sig_cells = []
    line_cells = []
    name_cells = []
    for label in labels:
        sig_cells.append(Paragraph(esc(str(label)), sty["center"]))
        line_cells.append(Spacer(1, 16 * mm))
        line = Table([[Paragraph(esc(footer_name), sty["center"])]], colWidths=[line_width])
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
    return sig


def _element_flowable(el: dict, context: dict, design: dict, sty: dict, col_width: float, *, full_width: float):
    source = str(el.get("source") or "").strip()
    label = str(el.get("label") or "").strip()
    bold = _bool(el.get("bold"), False)
    el_type = str(el.get("type") or "").strip()
    static_text = str(el.get("text") or "").strip()
    spec = field_spec(source)

    if source == SOURCE_PO_SIGNATURES_BLOCK or (spec and spec.get("value_type") == VALUE_TYPE_PO_SIGNATURES):
        return _po_signatures_block(resolve_source_value(context, SOURCE_PO_SIGNATURES_BLOCK), sty, full_width)

    if el_type == ELEMENT_TYPE_SIGNATURE or (spec and spec.get("value_type") == VALUE_TYPE_SIGNATURE):
        raw = resolve_source_value(context, source)
        data = raw if isinstance(raw, dict) else {}
        title = label or str(data.get("label") or "").strip() or (
            "Disiapkan Oleh" if source == SOURCE_SIGNATURE_PREPARED else "Disetujui Oleh"
        )
        footer = str(data.get("footer") or "").strip()
        middle = str(data.get("middle") or "").strip()
        image_bytes = data.get("image") if isinstance(data.get("image"), (bytes, bytearray)) else None
        return _signature_flowable(
            title=title,
            footer=footer,
            middle=middle,
            image_bytes=image_bytes,
            sty=sty,
            col_width=col_width,
        )

    if source == SOURCE_ESTIMASI_BLOCK or (spec and spec.get("value_type") == VALUE_TYPE_ESTIMASI):
        return _estimasi_box(resolve_source_value(context, SOURCE_ESTIMASI_BLOCK), sty, col_width)

    if el_type == ELEMENT_TYPE_STATIC or source == SOURCE_STATIC_TEXT:
        value = resolve_source_value(context, SOURCE_STATIC_TEXT, static_text=static_text)
        shown = str(value or "").strip()
        if label and shown:
            html = (
                f"{esc(label)}<br/><b>{esc(shown)}</b>"
                if not bold
                else f"<b>{esc(label)}</b><br/><b>{esc(shown)}</b>"
            )
            return Paragraph(html, sty["small"])
        if label and not shown:
            return _para(label, sty, bold=bold)
        return _para(shown, sty, bold=bold)

    if source == SOURCE_COMPANY_LOGO or (spec and spec.get("value_type") == VALUE_TYPE_IMAGE):
        return logo_flowable(
            design,
            width=min(36 * mm, col_width),
            height=12.2 * mm,
            company_style=sty["company"],
        )

    raw = resolve_source_value(context, source, static_text=static_text)
    if spec and spec.get("value_type") == VALUE_TYPE_MULTILINE:
        value_html = _multiline_html(str(raw or ""))
    else:
        value_html = esc(str(raw or "").strip()) or "&nbsp;"

    if source == SOURCE_COMPANY_NAME:
        name = str(raw or "").strip()
        if label:
            return Paragraph(
                f"{esc(label)}<br/><font size='11'><b>{esc(name)}</b></font>",
                sty["small"],
            )
        return Paragraph(f"<font size='11'><b>{esc(name)}</b></font>", sty["small"])

    if source == SOURCE_COMPANY_ADDRESS:
        return Paragraph(value_html, sty["address"])

    if label:
        inner = value_html if (spec and spec.get("value_type") == VALUE_TYPE_MULTILINE) else f"<b>{value_html}</b>"
        return Paragraph(f"{esc(label)}<br/>{inner}", sty["small"])

    if bold and not (spec and spec.get("value_type") == VALUE_TYPE_MULTILINE):
        return Paragraph(f"<b>{value_html}</b>", sty["small"])
    return Paragraph(
        value_html,
        sty["small"] if not (spec and spec.get("value_type") == VALUE_TYPE_MULTILINE) else sty["address"],
    )


def build_generic_invoice_footer_flowables(
    *,
    elements: Sequence[dict],
    context: dict,
    design: dict,
    sty: dict,
    width: float,
) -> List[Any]:
    """Return Platypus flowables for the generic footer (3-column elements)."""
    out: List[Any] = []
    visible = _visible_elements(elements)
    if not visible:
        return out
    # Full-width PO signature block alone uses legacy 3-slot proportions.
    only_po_sig = (
        len(visible) == 1
        and str(visible[0].get("source") or "").strip() == SOURCE_PO_SIGNATURES_BLOCK
    )
    if only_po_sig:
        out.append(
            _element_flowable(visible[0], context, design, sty, width, full_width=width)
        )
        return out

    groups = _group_by_align(visible)
    col_w = width / 3.0

    left_items = [_element_flowable(el, context, design, sty, col_w, full_width=width) for el in groups[ALIGN_LEFT]]
    center_items = [_element_flowable(el, context, design, sty, col_w, full_width=width) for el in groups[ALIGN_CENTER]]
    right_items = [_element_flowable(el, context, design, sty, col_w, full_width=width) for el in groups[ALIGN_RIGHT]]

    def col(items: List[Any]) -> Table:
        if not items:
            items = [Paragraph("&nbsp;", sty["small"])]
        rows = [[item] for item in items]
        table = Table(rows, colWidths=[col_w])
        table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 1),
            ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ]))
        return table

    grid = Table(
        [[col(left_items), col(center_items), col(right_items)]],
        colWidths=[col_w, col_w, col_w],
    )
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    out.append(grid)
    return out
