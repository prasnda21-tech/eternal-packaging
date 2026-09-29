"""Band renderer. The same design document drives preview and the final PDF."""
from __future__ import annotations

import io
from html import escape
from typing import Any, Dict, List

from reportlab.lib.colors import Color, HexColor
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from services.print_template_model import (
    BANDS,
    FONTS,
    format_date_value,
    format_money,
    format_qty,
    inner_size_mm,
    margins_mm,
    paper_size_mm,
    validate_design,
)

MM = 72.0 / 25.4


class DesignRenderError(Exception):
    def __init__(self, message: str, errors: List[dict] | None = None):
        super().__init__(message)
        self.errors = errors or [{"code": "render", "message": message, "element_id": ""}]


def _mm(value: float) -> float:
    return float(value) * MM


def _color(value: str, fallback: str = "#0f172a") -> Color:
    try:
        return HexColor(str(value or fallback))
    except Exception:
        return HexColor(fallback)


def _font_name(props: dict) -> str:
    family = FONTS.get(props.get("font") or "Helvetica") or FONTS["Helvetica"]
    bold = bool(props.get("bold"))
    italic = bool(props.get("italic")) and family.get("italic_supported")
    if bold and italic:
        return family["bold_italic"]
    if bold:
        return family["bold"]
    if italic:
        return family["italic"]
    return family["regular"]


def _align(value: str) -> int:
    if value == "center":
        return TA_CENTER
    if value == "right":
        return TA_RIGHT
    return TA_LEFT


def _style(props: dict) -> ParagraphStyle:
    size = float(props.get("font_size") or 8.5)
    return ParagraphStyle(
        "pt",
        fontName=_font_name(props),
        fontSize=size,
        leading=size + 2,
        alignment=_align(props.get("align") or "left"),
        textColor=_color(props.get("color") or "#0f172a"),
    )


def _paragraph(text: str, props: dict) -> Paragraph:
    safe = escape(str(text if text is not None else "")).replace("\n", "<br/>")
    return Paragraph(safe or "&nbsp;", _style(props))


def _text_height_mm(text: str, props: dict, width_mm: float) -> float:
    padding = float(props.get("padding_mm") or 0.4)
    width = max(_mm(width_mm - padding * 2), 4)
    paragraph = _paragraph(text, props)
    _w, height = paragraph.wrap(width, 5000)
    return max(float(props.get("min_height_mm") or 0), (height / MM) + padding * 2)


def _display_value(key: str, raw: Any, props: dict, context: dict) -> str:
    if key == "invoice.other_charges":
        rows = context.get("charges") or []
        if not rows:
            return "-"
        lines = []
        for row in rows:
            lines.append(f"{row['description']}: {format_money(row['amount'])}")
        return "\n".join(lines)
    if key in ("invoice.date", "invoice.due_date", "delivery.date"):
        return format_date_value(raw, props.get("date_format") or "dd/mm/yy")
    number_format = props.get("number_format") or ""
    if key in ("invoice.discount_amount",) and number_format == "rupiah":
        amount = float(raw or 0)
        if amount:
            return "- " + format_money(amount)
        return format_money(0)
    if number_format == "rupiah" or key.endswith("_amount") or key in ("invoice.subtotal", "invoice.grand_total", "invoice.paid_total", "invoice.outstanding_amount", "line.unit_price", "line.line_subtotal"):
        if number_format == "qty":
            return format_qty(raw)
        if number_format == "plain":
            return str(raw if raw is not None else "")
        if number_format == "rupiah" or key.startswith("invoice.") and key not in ("invoice.number", "invoice.date", "invoice.customer", "invoice.address", "invoice.po_number", "invoice.payment_term_days", "invoice.due_date", "invoice.sales", "invoice.payment_status", "invoice.notes", "invoice.terbilang", "invoice.ppn_percent"):
            return format_money(raw)
    if number_format == "qty" or key in ("line.qty", "delivery.total_qty"):
        return format_qty(raw)
    if raw is None or raw == "":
        return "-"
    return str(raw)


def _element_text(el: dict, context: dict, page_no: int = 1, page_count: int = 1) -> str:
    props = el.get("props") or {}
    if el.get("type") == "text":
        return str(props.get("text") or "")
    if el.get("type") != "field":
        return ""
    key = str(props.get("field") or "")
    if key == "page.number":
        return str(page_no)
    if key == "page.count":
        return str(page_count)
    if key == "page.label":
        return f"Halaman {page_no} dari {page_count}"
    return _display_value(key, (context.get("values") or {}).get(key, ""), props, context)


def _grow_elements(elements: List[dict], context: dict, default_props_width: bool = True) -> tuple:
    """Push later elements down when wrapped text is taller than the designed box."""
    prepared = []
    growths: List[tuple] = []
    ordered = sorted(elements, key=lambda item: (float(item.get("y_mm") or 0), int(item.get("z") or 0)))
    for el in ordered:
        props = el.get("props") or {}
        y0 = float(el.get("y_mm") or 0)
        shift = sum(extra for threshold, extra in growths if y0 >= threshold - 0.05)
        height = float(el.get("h_mm") or 0)
        if el.get("type") in ("text", "field") and props.get("wrap", True):
            text = _element_text(el, context)
            needed = _text_height_mm(text, props, float(el.get("w_mm") or 10))
            if needed > height:
                growths.append((y0 + height, needed - height))
                height = needed
        prepared.append({**el, "y_mm": y0 + shift, "h_mm": height, "_text": _element_text(el, context)})
    bottom = 0.0
    for el in prepared:
        bottom = max(bottom, float(el["y_mm"]) + float(el["h_mm"]))
    return prepared, bottom


def _layout_band(band: dict, context: dict) -> dict:
    elements, bottom = _grow_elements(list(band.get("elements") or []), context)
    height = max(float(band.get("height_mm") or 0), bottom)
    return {"height": height, "elements": elements}


def _column_text(column: dict, line: dict, props: dict, context: dict) -> str:
    key = column.get("field")
    raw = line.get(key, "")
    col_props = {
        "font": props.get("font") or "Helvetica",
        "font_size": props.get("font_size") or 8,
        "bold": key == "line.item_name",
        "align": column.get("align") or "left",
        "color": "#0f172a",
        "number_format": "rupiah" if key in ("line.unit_price", "line.line_subtotal") else ("qty" if key == "line.qty" else "plain"),
        "date_format": "dd/mm/yy",
    }
    return _display_value(key, raw, col_props, context)


def _layout_rows(table: dict, context: dict) -> List[dict]:
    props = table.get("props") or {}
    columns = props.get("columns") or []
    min_h = float(props.get("row_height_mm") or 7)
    rows = []
    for line in context.get("lines") or []:
        height = min_h
        cells = []
        for column in columns:
            text = _column_text(column, line, props, context)
            cell_props = {
                "font": props.get("font") or "Helvetica",
                "font_size": props.get("font_size") or 8,
                "bold": column.get("field") == "line.item_name",
                "align": column.get("align") or "left",
                "color": "#0f172a",
                "padding_mm": 0.6,
            }
            needed = _text_height_mm(text, cell_props, float(column.get("width_mm") or 10))
            height = max(height, needed)
            cells.append({"text": text, "props": cell_props, "column": column})
        rows.append({"height": height, "cells": cells})
    return rows


def _with_disclosure(summary_band: dict, context: dict) -> dict:
    if context.get("document_type") != "sales_invoice":
        return summary_band
    elements = list(summary_band.get("elements") or [])
    present = set()
    for el in elements:
        if el.get("type") == "field":
            present.add(str((el.get("props") or {}).get("field") or ""))
    bottom = 0.0
    for el in elements:
        bottom = max(bottom, float(el.get("y_mm") or 0) + float(el.get("h_mm") or 0))
    extras = []
    cursor = bottom + 1
    if context.get("discount", 0) > 0 and "invoice.discount_amount" not in present:
        extras.extend(_disclosure_pair("disc", cursor, "Diskon", "- " + format_money(context.get("discount"))))
        cursor += 6
    if context.get("ppn_amount", 0) > 0 and "invoice.ppn_amount" not in present:
        label = f"Pajak ({context.get('ppn_percent') or 0}%)"
        extras.extend(_disclosure_pair("ppn", cursor, label, format_money(context.get("ppn_amount"))))
        cursor += 6
    if context.get("charges") and "invoice.other_charges" not in present:
        for index, charge in enumerate(context["charges"]):
            extras.extend(_disclosure_pair(f"chg{index}", cursor, charge["description"], format_money(charge["amount"])))
            cursor += 6
    if not extras:
        return summary_band
    band = dict(summary_band)
    band["elements"] = elements + extras
    band["height_mm"] = max(float(summary_band.get("height_mm") or 0), cursor)
    return band


def _disclosure_pair(suffix: str, y: float, label: str, value: str) -> list:
    from services.print_template_model import _el

    return [
        _el(f"disc_l_{suffix}", "text", "summary", 70, y, 60, 5.5, text=label, bold=True, font_size=8, align="right", font="Helvetica"),
        _el(f"disc_v_{suffix}", "text", "summary", 132, y, 48, 5.5, text=value, bold=True, font_size=8, align="right", font="Helvetica"),
    ]


def _paginate(design: dict, context: dict) -> List[dict]:
    bands = design["bands"]
    title = _layout_band(bands["title"], context)
    header = _layout_band(bands["page_header"], context)
    footer = _layout_band(bands["page_footer"], context)
    summary = _layout_band(_with_disclosure(bands["summary"], context), context)
    table = next(el for el in bands["detail"]["elements"] if el.get("type") == "table")
    rows = _layout_rows(table, context)
    _pw, paper_h = paper_size_mm(design)
    margin = margins_mm(design)
    header_row_h = float(table.get("h_mm") or 8)

    pages: List[dict] = []
    index = 0
    first = True
    summary_done = False
    guard = 0
    while not summary_done:
        guard += 1
        if guard > 80:
            raise DesignRenderError("Dokumen menghasilkan terlalu banyak halaman.")
        chrome = (title["height"] if first else 0) + header["height"]
        body_top = margin["top"] + chrome
        body_bottom = paper_h - margin["bottom"] - footer["height"]
        body_h = body_bottom - body_top
        if body_h <= header_row_h:
            raise DesignRenderError("Susunan band tidak menyisakan ruang cetak yang cukup.")
        page = {
            "first": first,
            "title": title if first else None,
            "header": header,
            "footer": footer,
            "table": table,
            "rows": [],
            "summary": None,
            "body_top": body_top,
            "show_table": False,
            "summary_y": 0,
        }
        y = 0.0
        if index < len(rows) or not pages:
            page["show_table"] = True
            y = header_row_h
        while index < len(rows):
            row_h = rows[index]["height"]
            if y + row_h > body_h + 0.2:
                if not page["rows"]:
                    raise DesignRenderError("Ada baris rincian yang lebih tinggi daripada area cetak. Perkecil font atau perlebar kolom nama.")
                break
            page["rows"].append(rows[index])
            y += row_h
            index += 1
        if index >= len(rows) and summary["height"] <= body_h - y + 0.2:
            page["summary"] = summary
            page["summary_y"] = y
            summary_done = True
        elif index >= len(rows) and not page["rows"]:
            raise DesignRenderError("Ringkasan tidak muat pada halaman.")
        pages.append(page)
        first = False
    return pages


def _draw_paragraph(c: canvas.Canvas, text: str, props: dict, x_mm: float, y_mm: float, w_mm: float, h_mm: float, page_h_mm: float):
    padding = float(props.get("padding_mm") or 0.4)
    if props.get("background"):
        c.setFillColor(_color(props.get("background"), "#ffffff"))
        c.rect(_mm(x_mm), _mm(page_h_mm - (y_mm + h_mm)), _mm(w_mm), _mm(h_mm), stroke=0, fill=1)
    if props.get("border"):
        c.setStrokeColor(_color(props.get("border_color") or "#cbd5e1"))
        c.setLineWidth(float(props.get("border_width") or 0.4))
        c.rect(_mm(x_mm), _mm(page_h_mm - (y_mm + h_mm)), _mm(w_mm), _mm(h_mm), stroke=1, fill=0)
    paragraph = _paragraph(text, props)
    width = max(_mm(w_mm - padding * 2), 4)
    _w, height = paragraph.wrap(width, _mm(max(h_mm - padding * 2, 1)))
    top = _mm(page_h_mm - y_mm - padding)
    paragraph.drawOn(c, _mm(x_mm + padding), top - height)


def _draw_elements(c, elements, origin_x, origin_y, page_h):
    for el in sorted(elements, key=lambda item: int(item.get("z") or 0)):
        x = origin_x + float(el.get("x_mm") or 0)
        y = origin_y + float(el.get("y_mm") or 0)
        w = float(el.get("w_mm") or 0)
        h = float(el.get("h_mm") or 0)
        props = el.get("props") or {}
        kind = el.get("type")
        if kind in ("text", "field"):
            _draw_paragraph(c, el.get("_text") or "", props, x, y, w, h, page_h)
        elif kind == "line":
            c.setStrokeColor(_color(props.get("color") or "#0284c7"))
            c.setLineWidth(float(props.get("stroke") or 0.8))
            c.line(_mm(x), _mm(page_h - (y + h / 2)), _mm(x + w), _mm(page_h - (y + h / 2)))
        elif kind == "box":
            c.setStrokeColor(_color(props.get("border_color") or props.get("color") or "#cbd5e1"))
            c.setLineWidth(float(props.get("border_width") or 0.6))
            if props.get("background"):
                c.setFillColor(_color(props.get("background")))
                c.rect(_mm(x), _mm(page_h - (y + h)), _mm(w), _mm(h), stroke=1, fill=1)
            else:
                c.rect(_mm(x), _mm(page_h - (y + h)), _mm(w), _mm(h), stroke=1, fill=0)
        elif kind == "image":
            _draw_image(c, props, x, y, w, h, page_h)
        elif kind == "qr":
            _draw_qr(c, props, x, y, w, h, page_h, el.get("_tracking_url") or "")
        elif kind == "signature":
            _draw_signature(c, el, x, y, w, h, page_h)


def _draw_image(c, props, x, y, w, h, page_h):
    blob = props.get("_bytes")
    if not blob:
        return
    try:
        image = ImageReader(io.BytesIO(blob))
        c.drawImage(image, _mm(x), _mm(page_h - (y + h)), width=_mm(w), height=_mm(h), preserveAspectRatio=True, mask="auto", anchor="c")
    except Exception:
        return


def _draw_qr(c, props, x, y, w, h, page_h, url: str):
    if not url:
        if props.get("_preview"):
            c.setStrokeColor(_color("#94a3b8"))
            c.setDash(1, 2)
            c.rect(_mm(x), _mm(page_h - (y + h)), _mm(w), _mm(h), stroke=1, fill=0)
            c.setDash()
            _draw_paragraph(c, "QR tracking", {"font": "Helvetica", "font_size": 7, "align": "center", "color": "#64748b"}, x, y + h / 2 - 2, w, 5, page_h)
        return
    try:
        import qrcode

        image = qrcode.make(url)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        buffer.seek(0)
        c.drawImage(ImageReader(buffer), _mm(x), _mm(page_h - (y + h)), width=_mm(w), height=_mm(h), preserveAspectRatio=True, mask="auto")
    except Exception as exc:
        raise DesignRenderError("QR tracking gagal digambar.") from exc


def _draw_signature(c, el, x, y, w, h, page_h):
    props = el.get("props") or {}
    c.setStrokeColor(_color("#cbd5e1"))
    c.rect(_mm(x), _mm(page_h - (y + h)), _mm(w), _mm(h), stroke=1, fill=0)
    _draw_paragraph(c, str(props.get("label") or "Tanda tangan"), {"font": "Helvetica", "font_size": 8, "bold": True, "align": "center"}, x, y + 1, w, 5, page_h)
    if props.get("_bytes"):
        _draw_image(c, props, x + 4, y + 6, w - 8, max(h - 16, 8), page_h)
    _draw_paragraph(c, str(props.get("name") or ""), {"font": "Helvetica", "font_size": 8, "align": "center", "bold": True}, x, y + h - 10, w, 4.5, page_h)
    _draw_paragraph(c, str(props.get("title") or ""), {"font": "Helvetica", "font_size": 7, "align": "center", "color": "#64748b"}, x, y + h - 5.5, w, 4, page_h)


def _draw_table(c, table, rows, x, y, page_h):
    props = table.get("props") or {}
    columns = props.get("columns") or []
    header_h = float(table.get("h_mm") or 8)
    c.setFillColor(_color(props.get("header_fill") or "#f1f5f9"))
    c.rect(_mm(x), _mm(page_h - (y + header_h)), _mm(float(table.get("w_mm") or 0)), _mm(header_h), stroke=0, fill=1)
    cursor = x
    header_props = {"font": props.get("font") or "Helvetica", "font_size": props.get("font_size") or 8, "bold": True, "color": "#1e293b"}
    for column in columns:
        width = float(column.get("width_mm") or 0)
        _draw_paragraph(c, str(column.get("label") or ""), {**header_props, "align": column.get("align") or "left"}, cursor, y, width, header_h, page_h)
        cursor += width
    y_row = y + header_h
    for row in rows:
        cursor = x
        for cell in row["cells"]:
            width = float(cell["column"].get("width_mm") or 0)
            _draw_paragraph(c, cell["text"], cell["props"], cursor, y_row, width, row["height"], page_h)
            cursor += width
        y_row += row["height"]
    c.setStrokeColor(_color(props.get("border_color") or "#e2e8f0"))
    c.setLineWidth(0.4)
    total_h = header_h + sum(row["height"] for row in rows)
    c.rect(_mm(x), _mm(page_h - (y + total_h)), _mm(float(table.get("w_mm") or 0)), _mm(total_h), stroke=1, fill=0)
    cursor = x
    for column in columns[:-1]:
        cursor += float(column.get("width_mm") or 0)
        c.line(_mm(cursor), _mm(page_h - y), _mm(cursor), _mm(page_h - (y + total_h)))


def _attach_runtime(elements, context, assets):
    assets = assets or {}
    for el in elements:
        props = dict(el.get("props") or {})
        if el.get("type") in ("image", "signature"):
            asset_id = str(props.get("asset_id") or "")
            props["_bytes"] = assets.get(asset_id)
        if el.get("type") == "qr":
            el["_tracking_url"] = context.get("tracking_url") or ""
            props["_preview"] = bool(context.get("preview"))
        el["props"] = props
        if el.get("type") in ("text", "field"):
            el["_text"] = _element_text(el, context)
    return elements


def render_design_pdf(design: dict, context: dict, assets: dict | None = None) -> bytes:
    errors = validate_design(design, context.get("document_type"))
    if errors:
        raise DesignRenderError(errors[0]["message"], errors)
    pages = _paginate(design, context)
    page_count = len(pages)
    paper_w, paper_h = paper_size_mm(design)
    margin = margins_mm(design)
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(_mm(paper_w), _mm(paper_h)))
    pdf.setTitle("DATA CONTOH" if context.get("sample") else "Eternal Packaging")
    for number, page in enumerate(pages, start=1):
        cursor_y = margin["top"]
        if page.get("title"):
            title_elements = _retitle(page["title"]["elements"], context, number, page_count, assets)
            _draw_elements(pdf, title_elements, margin["left"], cursor_y, paper_h)
            cursor_y += page["title"]["height"]
        header_elements = _retitle(page["header"]["elements"], context, number, page_count, assets)
        _draw_elements(pdf, header_elements, margin["left"], cursor_y, paper_h)
        body_y = page["body_top"]
        table = page["table"]
        if page.get("show_table"):
            _draw_table(pdf, table, page["rows"], margin["left"] + float(table.get("x_mm") or 0), body_y, paper_h)
        if page.get("summary"):
            summary_y = body_y + page.get("summary_y", 0)
            summary_elements = _retitle(page["summary"]["elements"], context, number, page_count, assets)
            _draw_elements(pdf, summary_elements, margin["left"], summary_y, paper_h)
        footer_y = paper_h - margin["bottom"] - page["footer"]["height"]
        footer_elements = _retitle(page["footer"]["elements"], context, number, page_count, assets)
        _draw_elements(pdf, footer_elements, margin["left"], footer_y, paper_h)
        if number < page_count:
            pdf.showPage()
    pdf.save()
    data = buffer.getvalue()
    if not data.startswith(b"%PDF"):
        raise DesignRenderError("Renderer tidak menghasilkan PDF.")
    return data


def _retitle(elements, context, page_no, page_count, assets):
    copied = []
    for el in elements:
        item = dict(el)
        item["props"] = dict(el.get("props") or {})
        copied.append(item)
    copied = _attach_runtime(copied, context, assets)
    for el in copied:
        if el.get("type") in ("text", "field"):
            el["_text"] = _element_text(el, context, page_no, page_count)
    return copied


def measure_text_width(text: str, font: str, size: float) -> float:
    return pdfmetrics.stringWidth(text or "", font, size)
