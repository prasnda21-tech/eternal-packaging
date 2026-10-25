"""Controlled invoice/proforma header renderer for Design Cetakan Phase 1.

Groups visible elements into left / center / right columns by align.
No mm coordinates, no HTML, no arbitrary CSS.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from services.pdf_layout import esc, logo_flowable
from services.print_context_resolver import resolve_source_value
from services.print_field_registry import (
    ALIGN_CENTER,
    ALIGN_LEFT,
    ALIGN_RIGHT,
    ELEMENT_TYPE_STATIC,
    SOURCE_COMPANY_LOGO,
    SOURCE_COMPANY_NAME,
    SOURCE_COMPANY_ADDRESS,
    SOURCE_STATIC_TEXT,
    VALUE_TYPE_IMAGE,
    VALUE_TYPE_MULTILINE,
    field_spec,
)


def header_elements_active(elements: Any) -> bool:
    """True when a non-empty sanitized element list should drive the header."""
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


def _element_flowable(el: dict, context: dict, design: dict, sty: dict, col_width: float):
    source = str(el.get("source") or "").strip()
    label = str(el.get("label") or "").strip()
    bold = _bool(el.get("bold"), False)
    el_type = str(el.get("type") or "").strip()
    static_text = str(el.get("text") or "").strip()
    spec = field_spec(source)

    if el_type == ELEMENT_TYPE_STATIC or source == SOURCE_STATIC_TEXT:
        value = resolve_source_value(context, SOURCE_STATIC_TEXT, static_text=static_text)
        shown = str(value or "").strip()
        if label and shown:
            html = f"{esc(label)}<br/><b>{esc(shown)}</b>" if not bold else f"<b>{esc(label)}</b><br/><b>{esc(shown)}</b>"
            return Paragraph(html, sty["small"])
        if label and not shown:
            return _para(label, sty, bold=bold)
        return _para(shown, sty, bold=bold)

    if source == SOURCE_COMPANY_LOGO or (spec and spec.get("value_type") == VALUE_TYPE_IMAGE):
        # Reuse existing logo pipeline (custom asset → repo → text).
        return logo_flowable(design, width=min(36 * mm, col_width), height=12.2 * mm, company_style=sty["company"])

    raw = resolve_source_value(context, source, static_text=static_text)
    if spec and spec.get("value_type") == VALUE_TYPE_MULTILINE:
        value_html = _multiline_html(str(raw or ""))
    else:
        value_html = esc(str(raw or "").strip()) or "&nbsp;"

    if source == SOURCE_COMPANY_NAME:
        name = str(raw or "").strip()
        if label:
            return Paragraph(
                f"{esc(label)}<br/><font size='12'><b>{esc(name)}</b></font>",
                sty["address"],
            )
        return Paragraph(f"<font size='12'><b>{esc(name)}</b></font>", sty["address"])

    if source == SOURCE_COMPANY_ADDRESS:
        return Paragraph(value_html, sty["address"])

    if label:
        inner = value_html if (spec and spec.get("value_type") == VALUE_TYPE_MULTILINE) else f"<b>{value_html}</b>"
        return Paragraph(f"{esc(label)}<br/>{inner}", sty["small"])

    if bold and not (spec and spec.get("value_type") == VALUE_TYPE_MULTILINE):
        return Paragraph(f"<b>{value_html}</b>", sty["small"])
    return Paragraph(value_html, sty["small"] if not (spec and spec.get("value_type") == VALUE_TYPE_MULTILINE) else sty["address"])


def build_generic_invoice_header_flowables(
    *,
    elements: Sequence[dict],
    context: dict,
    design: dict,
    sty: dict,
    width: float,
    document_title: str,
    proforma_warning: Optional[str] = None,
) -> List[Any]:
    """Return Platypus flowables for the generic header (optional title + 3-column elements)."""
    out: List[Any] = []
    visible = _visible_elements(elements)
    groups = _group_by_align(visible)

    # Document title stays hardcoded when provided (business document identity).
    title = str(document_title or "").strip()
    is_markup = title == "Faktur Markup"
    warn_style = sty["markup_warn"] if is_markup and sty.get("markup_warn") else sty["warn"]
    if title:
        out.append(Paragraph(esc(title), sty["title"]))
        if proforma_warning:
            out.append(Paragraph(esc(proforma_warning), warn_style))
            if is_markup:
                out.append(Spacer(1, 1 * mm))
        out.append(Spacer(1, 2 * mm))
    elif proforma_warning:
        out.append(Paragraph(esc(proforma_warning), warn_style))
        out.append(Spacer(1, 2 * mm))

    col_w = width / 3.0
    left_items = [_element_flowable(el, context, design, sty, col_w) for el in groups[ALIGN_LEFT]]
    center_items = [_element_flowable(el, context, design, sty, col_w) for el in groups[ALIGN_CENTER]]
    right_items = [_element_flowable(el, context, design, sty, col_w) for el in groups[ALIGN_RIGHT]]

    def col(items: List[Any]) -> Table:
        if not items:
            blank = Paragraph("&nbsp;", sty["small"])
            items = [blank]
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

    grid = Table([[col(left_items), col(center_items), col(right_items)]], colWidths=[col_w, col_w, col_w])
    grid.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.black),
        ("LINEAFTER", (0, 0), (1, -1), 0.4, colors.black),
    ]))
    out.append(grid)
    out.append(Spacer(1, 2 * mm))
    return out
