"""Print-template design model shared by the canvas and the PDF renderer.

Units are millimetres. Zoom is an editor concern and is not stored here.
The model does not recompute accounting totals. It only selects values that
already exist on the invoice or delivery being printed.
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = 1

DOCUMENT_INVOICE = "sales_invoice"
DOCUMENT_DELIVERY = "sales_delivery"
DOCUMENT_TYPES = (DOCUMENT_INVOICE, DOCUMENT_DELIVERY)
AUDIENCES = ("customer", "internal")
STATUSES = ("draft", "active", "archived")
BANDS = ("title", "page_header", "detail", "summary", "page_footer")
ELEMENT_TYPES = ("text", "field", "image", "line", "box", "table", "qr", "signature")

# Same strings already printed by the official invoice and Surat Jalan PDFs.
# There is no separate company-profile document in this repository.
COMPANY_IDENTITY = {
    "name": "ETERNAL PACKAGING",
    "tagline": "Food Packaging & Printing Solutions",
}

PAPER_PRESETS = {
    "A4": (210.0, 297.0),
    "A5": (148.0, 210.0),
    "F4": (210.0, 330.0),
}
PAPER_MIN_MM = 70.0
PAPER_MAX_MM = 500.0

FONTS = {
    "Helvetica": {
        "regular": "Helvetica",
        "bold": "Helvetica-Bold",
        "italic": "Helvetica-Oblique",
        "bold_italic": "Helvetica-BoldOblique",
        "italic_supported": True,
    },
    "Times": {
        "regular": "Times-Roman",
        "bold": "Times-Bold",
        "italic": "Times-Italic",
        "bold_italic": "Times-BoldItalic",
        "italic_supported": True,
    },
    "Courier": {
        "regular": "Courier",
        "bold": "Courier-Bold",
        "italic": "Courier-Oblique",
        "bold_italic": "Courier-BoldOblique",
        "italic_supported": True,
    },
}

DATE_FORMATS = ("dd/mm/yy", "dd/mm/yyyy", "yyyy-mm-dd")
NUMBER_FORMATS = ("plain", "qty", "rupiah")
ALIGNMENTS = ("left", "center", "right")

REQUIRED_FIELDS = {
    DOCUMENT_INVOICE: (
        "invoice.number",
        "invoice.date",
        "invoice.customer",
        "line.item_name",
        "line.qty",
        "invoice.grand_total",
    ),
    DOCUMENT_DELIVERY: (
        "delivery.number",
        "delivery.date",
        "delivery.customer",
        "line.item_name",
        "line.qty",
    ),
}

LINE_FIELDS = {
    "line.index",
    "line.item_name",
    "line.qty",
    "line.unit",
    "line.unit_price",
    "line.line_subtotal",
    "line.item_code",
    "line.notes",
    "line.color",
    "line.process",
}

# Fields the public tracking PDF is allowed to read. Notes stay blank.
# HPP, margin, user identity, and other orders are intentionally absent.
PUBLIC_INVOICE_FIELDS = {
    "invoice.number",
    "invoice.date",
    "invoice.customer",
    "invoice.address",
    "invoice.po_number",
    "invoice.payment_term_days",
    "invoice.due_date",
    "invoice.sales",
    "invoice.payment_status",
    "invoice.notes",
    "invoice.subtotal",
    "invoice.discount_amount",
    "invoice.ppn_percent",
    "invoice.ppn_amount",
    "invoice.other_charges",
    "invoice.grand_total",
    "invoice.paid_total",
    "invoice.outstanding_amount",
    "invoice.terbilang",
    "line.item_name",
    "line.qty",
    "line.unit",
    "line.unit_price",
    "line.line_subtotal",
    "line.index",
    "company.name",
    "company.tagline",
    "page.number",
    "page.count",
    "page.label",
}


def _field(key, label, documents, *, kind="header", enabled=True, reason="", number_format="plain", required=False):
    return {
        "key": key,
        "label": label,
        "documents": documents,
        "kind": kind,
        "enabled": enabled,
        "reason": reason,
        "number_format": number_format,
        "required": required,
    }


_FIELDS = [
    _field("invoice.number", "Nomor faktur", (DOCUMENT_INVOICE,), required=True),
    _field("invoice.date", "Tanggal faktur", (DOCUMENT_INVOICE,), required=True),
    _field("invoice.customer", "Customer", (DOCUMENT_INVOICE,), required=True),
    _field("invoice.address", "Alamat", (DOCUMENT_INVOICE,)),
    _field("invoice.po_number", "Referensi PO", (DOCUMENT_INVOICE,)),
    _field("invoice.payment_term_days", "Termin (hari)", (DOCUMENT_INVOICE,)),
    _field("invoice.due_date", "Jatuh tempo", (DOCUMENT_INVOICE,)),
    _field("invoice.sales", "Sales", (DOCUMENT_INVOICE,)),
    _field("invoice.payment_status", "Status pembayaran", (DOCUMENT_INVOICE,)),
    _field("invoice.notes", "Catatan", (DOCUMENT_INVOICE,)),
    _field("invoice.subtotal", "Subtotal", (DOCUMENT_INVOICE,), number_format="rupiah"),
    _field("invoice.discount_amount", "Diskon", (DOCUMENT_INVOICE,), number_format="rupiah"),
    _field("invoice.ppn_percent", "Persen pajak", (DOCUMENT_INVOICE,)),
    _field("invoice.ppn_amount", "Pajak", (DOCUMENT_INVOICE,), number_format="rupiah"),
    _field("invoice.other_charges", "Biaya lainnya", (DOCUMENT_INVOICE,), number_format="rupiah"),
    _field("invoice.grand_total", "Total tagihan", (DOCUMENT_INVOICE,), number_format="rupiah", required=True),
    _field("invoice.paid_total", "Pembayaran diterima", (DOCUMENT_INVOICE,), number_format="rupiah"),
    _field("invoice.outstanding_amount", "Sisa tagihan", (DOCUMENT_INVOICE,), number_format="rupiah"),
    _field("invoice.terbilang", "Terbilang", (DOCUMENT_INVOICE,)),
    _field("invoice.bank_account", "Rekening pembayaran", (DOCUMENT_INVOICE,), enabled=False,
           reason="Sumber rekening untuk cetakan belum tersedia pada faktur."),
    _field("line.item_name", "Nama barang", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY), kind="line", required=True),
    _field("line.qty", "Qty", (DOCUMENT_INVOICE,), kind="line", number_format="qty", required=True),
    _field("line.qty", "Qty SJ", (DOCUMENT_DELIVERY,), kind="line", number_format="qty", required=True),
    _field("line.unit", "Satuan", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY), kind="line"),
    _field("line.unit_price", "Harga satuan", (DOCUMENT_INVOICE,), kind="line", number_format="rupiah"),
    _field("line.line_subtotal", "Jumlah per baris", (DOCUMENT_INVOICE,), kind="line", number_format="rupiah"),
    _field("line.index", "Nomor baris", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY), kind="line"),
    _field("line.item_code", "Kode barang", (DOCUMENT_DELIVERY,), kind="line"),
    _field("line.notes", "Catatan baris", (DOCUMENT_DELIVERY,), kind="line"),
    _field("line.color", "Warna", (DOCUMENT_DELIVERY,), kind="line"),
    _field("line.process", "Proses", (DOCUMENT_DELIVERY,), kind="line"),
    _field("delivery.number", "Nomor SJ", (DOCUMENT_DELIVERY,), required=True),
    _field("delivery.date", "Tanggal SJ", (DOCUMENT_DELIVERY,), required=True),
    _field("delivery.customer", "Customer / penerima", (DOCUMENT_DELIVERY,), required=True),
    _field("delivery.recipient", "Nama penerima", (DOCUMENT_DELIVERY,)),
    _field("delivery.address", "Alamat pengiriman", (DOCUMENT_DELIVERY,)),
    _field("delivery.po_number", "Referensi PO", (DOCUMENT_DELIVERY,)),
    _field("delivery.outlet", "Outlet", (DOCUMENT_DELIVERY,)),
    _field("delivery.notes", "Catatan", (DOCUMENT_DELIVERY,)),
    _field("delivery.sales", "Sales", (DOCUMENT_DELIVERY,)),
    _field("delivery.expedition", "Ekspedisi", (DOCUMENT_DELIVERY,)),
    _field("delivery.zone", "Zona kirim", (DOCUMENT_DELIVERY,)),
    _field("delivery.shipment_index", "Kirim ke", (DOCUMENT_DELIVERY,)),
    _field("delivery.total_qty", "Total qty SJ", (DOCUMENT_DELIVERY,), number_format="qty"),
    _field("delivery.unit_price", "Harga satuan", (DOCUMENT_DELIVERY,), enabled=False,
           reason="Harga tidak dicetak pada Surat Jalan pada tahap ini."),
    _field("company.name", "Nama perusahaan", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY)),
    _field("company.tagline", "Tagline perusahaan", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY)),
    _field("page.number", "Nomor halaman", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY)),
    _field("page.count", "Jumlah halaman", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY)),
    _field("page.label", "Halaman X dari Y", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY)),
    _field("qr.tracking", "QR tracking", (DOCUMENT_INVOICE, DOCUMENT_DELIVERY), kind="qr"),
]


def field_catalog(document_type: str) -> List[dict]:
    rows = []
    for spec in _FIELDS:
        if document_type not in spec["documents"]:
            continue
        rows.append({k: v for k, v in spec.items() if k != "documents"})
    return rows


def field_spec(document_type: str, key: str) -> Optional[dict]:
    for row in field_catalog(document_type):
        if row["key"] == key:
            return row
    return None


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def format_money(val: Any) -> str:
    """Same grouping as the official invoice PDF."""
    try:
        f_val = float(val if val is not None else 0)
    except (TypeError, ValueError):
        return "Rp 0"
    if f_val.is_integer():
        return "Rp " + f"{int(f_val):,}".replace(",", ".")
    text = f"{f_val:,.2f}"
    return "Rp " + text.replace(",", "X").replace(".", ",").replace("X", ".")


def format_qty(val: Any) -> str:
    try:
        f_val = float(val if val is not None else 0)
    except (TypeError, ValueError):
        return "0"
    if f_val.is_integer():
        return f"{int(f_val):,}".replace(",", ".")
    text = f"{f_val:,.2f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def terbilang_rupiah(value: Any) -> str:
    """Indonesian spelling of a stored money amount. Not an accounting recalculation."""
    try:
        amount = int(round(float(value if value is not None else 0)))
    except (TypeError, ValueError):
        return ""
    words = _terbilang_int(abs(amount)) or "nol"
    if amount < 0:
        words = "minus " + words
    return words + " rupiah"


def _terbilang_int(n: int) -> str:
    angka = ["", "satu", "dua", "tiga", "empat", "lima", "enam", "tujuh", "delapan", "sembilan", "sepuluh", "sebelas"]
    if n < 12:
        return angka[n]
    if n < 20:
        return angka[n - 10] + " belas"
    if n < 100:
        tail = n % 10
        return angka[n // 10] + " puluh" + ((" " + angka[tail]) if tail else "")
    if n < 200:
        return "seratus" + ((" " + _terbilang_int(n - 100)) if n > 100 else "")
    if n < 1000:
        tail = n % 100
        return angka[n // 100] + " ratus" + ((" " + _terbilang_int(tail)) if tail else "")
    if n < 2000:
        return "seribu" + ((" " + _terbilang_int(n - 1000)) if n > 1000 else "")
    scales = ((10**12, "triliun"), (10**9, "miliar"), (10**6, "juta"), (10**3, "ribu"))
    for size, name in scales:
        if n >= size:
            head = n // size
            tail = n % size
            return _terbilang_int(head) + " " + name + ((" " + _terbilang_int(tail)) if tail else "")
    return str(n)


def format_date_value(raw: Any, fmt: str = "dd/mm/yy") -> str:
    from datetime import date, datetime
    from services.pdf_date import format_pdf_date

    if fmt in (None, "", "dd/mm/yy"):
        text = format_pdf_date(raw, fallback="")
        return text or "-"
    parsed = None
    if isinstance(raw, datetime):
        parsed = raw.date()
    elif isinstance(raw, date):
        parsed = raw
    else:
        text = str(raw or "").strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            try:
                parsed = date(int(text[0:4]), int(text[5:7]), int(text[8:10]))
            except ValueError:
                parsed = None
    if not parsed:
        return str(raw or "-") or "-"
    if fmt == "dd/mm/yyyy":
        return parsed.strftime("%d/%m/%Y")
    if fmt == "yyyy-mm-dd":
        return parsed.strftime("%Y-%m-%d")
    return format_pdf_date(parsed)


def paper_size_mm(design: dict) -> tuple:
    paper = (design or {}).get("paper") or {}
    preset = str(paper.get("preset") or "A4")
    if preset in PAPER_PRESETS:
        width, height = PAPER_PRESETS[preset]
    elif preset == "custom":
        width, height = _num(paper.get("width_mm"), 0), _num(paper.get("height_mm"), 0)
    else:
        width, height = _num(paper.get("width_mm"), 0), _num(paper.get("height_mm"), 0)
    if str(paper.get("orientation") or "portrait") == "landscape":
        width, height = height, width
    return width, height


def margins_mm(design: dict) -> dict:
    paper = (design or {}).get("paper") or {}
    return {
        "top": _num(paper.get("margin_top_mm"), 15),
        "right": _num(paper.get("margin_right_mm"), 15),
        "bottom": _num(paper.get("margin_bottom_mm"), 15),
        "left": _num(paper.get("margin_left_mm"), 15),
    }


def inner_size_mm(design: dict) -> tuple:
    width, height = paper_size_mm(design)
    margin = margins_mm(design)
    return width - margin["left"] - margin["right"], height - margin["top"] - margin["bottom"]


def _el(eid, etype, band, x, y, w, h, **props):
    required = bool(props.pop("required", False))
    z = int(props.pop("z", 1))
    return {
        "id": eid,
        "type": etype,
        "band": band,
        "x_mm": float(x),
        "y_mm": float(y),
        "w_mm": float(w),
        "h_mm": float(h),
        "z": z,
        "required": required,
        "props": props,
    }


def _text(eid, band, x, y, w, h, text, **extra):
    props = {
        "text": text,
        "font": extra.pop("font", "Helvetica"),
        "font_size": extra.pop("font_size", 9),
        "bold": extra.pop("bold", False),
        "italic": extra.pop("italic", False),
        "align": extra.pop("align", "left"),
        "color": extra.pop("color", "#0f172a"),
        "wrap": True,
    }
    props.update(extra)
    return _el(eid, "text", band, x, y, w, h, **props)


def _field(eid, band, x, y, w, h, key, **extra):
    props = {
        "field": key,
        "font": extra.pop("font", "Helvetica"),
        "font_size": extra.pop("font_size", 8.5),
        "bold": extra.pop("bold", False),
        "italic": False,
        "align": extra.pop("align", "left"),
        "color": extra.pop("color", "#0f172a"),
        "wrap": True,
        "date_format": extra.pop("date_format", "dd/mm/yy"),
        "number_format": extra.pop("number_format", ""),
    }
    props.update(extra)
    return _el(eid, "field", band, x, y, w, h, **props)


def _paper(preset="A4"):
    width, height = PAPER_PRESETS[preset]
    return {
        "preset": preset,
        "orientation": "portrait",
        "width_mm": width,
        "height_mm": height,
        "margin_top_mm": 15,
        "margin_right_mm": 15,
        "margin_bottom_mm": 15,
        "margin_left_mm": 15,
    }


def builtin_invoice_design() -> dict:
    """Visual stand-in for the official A4 invoice. Operational fallback stays on the legacy renderer."""
    title = [
        _text("t_company", "title", 0, 0, 100, 7, "ETERNAL PACKAGING", bold=True, font_size=14),
        _text("t_doc", "title", 100, 0, 80, 6, "FAKTUR PENJUALAN", bold=True, font_size=12, align="right", color="#9333ea"),
        _field("t_number", "title", 100, 6.5, 80, 6, "invoice.number", bold=True, align="right", color="#7e22ce", required=True),
        _el("t_rule", "line", "title", 0, 14.2, 180, 1.2, color="#a855f7", stroke=1.2),
    ]
    header = []
    left = [
        ("Nomor PO", "invoice.po_number", False),
        ("Tanggal", "invoice.date", True),
        ("Termin (hari)", "invoice.payment_term_days", False),
        ("Jatuh tempo", "invoice.due_date", False),
        ("Catatan", "invoice.notes", False),
    ]
    right = [
        ("Customer", "invoice.customer", True),
        ("Alamat", "invoice.address", False),
        ("Sales", "invoice.sales", False),
        ("Status", "invoice.payment_status", False),
    ]
    for index, (label, key, required) in enumerate(left):
        y = index * 7.2
        header.append(_text(f"h_l_{index}", "page_header", 0, y, 28, 6.5, label, bold=True, font_size=8))
        header.append(_field(f"h_lv_{index}", "page_header", 28, y, 60, 6.5, key, required=required, font_size=8))
    for index, (label, key, required) in enumerate(right):
        y = index * 7.2
        header.append(_text(f"h_r_{index}", "page_header", 92, y, 26, 6.5, label, bold=True, font_size=8))
        header.append(_field(f"h_rv_{index}", "page_header", 118, y, 62, 6.5, key, required=required, font_size=8))
    table = _el(
        "d_table",
        "table",
        "detail",
        0, 0, 180, 8,
        required=True,
        header_fill="#f1f5f9",
        border_color="#e2e8f0",
        font="Helvetica",
        font_size=8,
        row_height_mm=7,
        columns=[
            {"id": "c_no", "field": "line.index", "label": "No", "width_mm": 10, "align": "left"},
            {"id": "c_name", "field": "line.item_name", "label": "Produk / Jasa", "width_mm": 64, "align": "left"},
            {"id": "c_qty", "field": "line.qty", "label": "Qty", "width_mm": 18, "align": "right"},
            {"id": "c_unit", "field": "line.unit", "label": "Satuan", "width_mm": 18, "align": "left"},
            {"id": "c_price", "field": "line.unit_price", "label": "Harga Satuan", "width_mm": 34, "align": "right"},
            {"id": "c_amount", "field": "line.line_subtotal", "label": "Jumlah", "width_mm": 36, "align": "right"},
        ],
    )
    summary_rows = [
        ("Subtotal", "invoice.subtotal", False),
        ("Diskon", "invoice.discount_amount", False),
        ("Pajak", "invoice.ppn_amount", False),
        ("Biaya lainnya", "invoice.other_charges", False),
        ("Total", "invoice.grand_total", True),
        ("Pembayaran", "invoice.paid_total", False),
        ("Sisa tagihan", "invoice.outstanding_amount", False),
    ]
    summary = []
    for index, (label, key, required) in enumerate(summary_rows):
        y = index * 5.6
        summary.append(_text(f"s_l_{index}", "summary", 90, y, 40, 5.2, label, bold=True, font_size=8, align="right"))
        summary.append(_field(
            f"s_v_{index}", "summary", 132, y, 48, 5.2, key,
            required=required, align="right", bold=required, font_size=8,
            number_format="rupiah",
        ))
    summary.append(_text("s_terbilang_l", "summary", 0, 40, 24, 6, "Terbilang", bold=True, font_size=8))
    summary.append(_field("s_terbilang", "summary", 24, 40, 156, 8, "invoice.terbilang", font_size=8))
    footer = [
        _field("f_page", "page_footer", 0, 1, 180, 6, "page.label", align="center", font_size=8, color="#64748b"),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "document_type": DOCUMENT_INVOICE,
        "paper": _paper("A4"),
        "grid": {"enabled": True, "snap": True, "snap_mm": 1},
        "bands": {
            "title": {"height_mm": 18, "elements": title},
            "page_header": {"height_mm": 38, "elements": header},
            "detail": {"height_mm": 8, "elements": [table]},
            "summary": {"height_mm": 50, "elements": summary},
            "page_footer": {"height_mm": 8, "elements": footer},
        },
    }


def builtin_delivery_design() -> dict:
    title = [
        _text("t_company", "title", 0, 0, 100, 6, "ETERNAL PACKAGING", bold=True, font_size=14),
        _text("t_tag", "title", 0, 6.2, 100, 5, "Food Packaging & Printing Solutions", font_size=7.5, color="#64748b"),
        _text("t_doc", "title", 100, 0, 80, 6, "SURAT JALAN", bold=True, font_size=12, align="right", color="#0369a1"),
        _field("t_number", "title", 100, 6.5, 80, 6, "delivery.number", bold=True, align="right", color="#0284c7", required=True),
        _el("t_rule", "line", "title", 0, 13.5, 180, 1.2, color="#0284c7", stroke=1.2),
    ]
    pairs = [
        (0, "Nomor PO", "delivery.po_number", "Customer", "delivery.customer", False, True),
        (1, "Tanggal", "delivery.date", "Penerima", "delivery.recipient", True, False),
        (2, "Alamat kirim", "delivery.address", "Outlet", "delivery.outlet", False, False),
        (3, "Ekspedisi", "delivery.expedition", "Zona", "delivery.zone", False, False),
    ]
    header = []
    for index, left_label, left_key, right_label, right_key, left_req, right_req in pairs:
        y = index * 7.4
        header.append(_text(f"h_l_{index}", "page_header", 0, y, 28, 6.5, left_label, bold=True, font_size=8))
        header.append(_field(f"h_lv_{index}", "page_header", 28, y, 60, 6.5, left_key, required=left_req, font_size=8))
        header.append(_text(f"h_r_{index}", "page_header", 92, y, 24, 6.5, right_label, bold=True, font_size=8))
        header.append(_field(f"h_rv_{index}", "page_header", 116, y, 64, 6.5, right_key, required=right_req, font_size=8))
    table = _el(
        "d_table",
        "table",
        "detail",
        0, 0, 180, 8,
        required=True,
        header_fill="#f1f5f9",
        border_color="#e2e8f0",
        font="Helvetica",
        font_size=8,
        row_height_mm=7,
        columns=[
            {"id": "c_no", "field": "line.index", "label": "No", "width_mm": 12, "align": "left"},
            {"id": "c_name", "field": "line.item_name", "label": "Barang", "width_mm": 78, "align": "left"},
            {"id": "c_qty", "field": "line.qty", "label": "Qty SJ", "width_mm": 24, "align": "right"},
            {"id": "c_unit", "field": "line.unit", "label": "Satuan", "width_mm": 22, "align": "left"},
            {"id": "c_notes", "field": "line.notes", "label": "Catatan", "width_mm": 44, "align": "left"},
        ],
    )
    summary = [
        _text("s_notes_l", "summary", 0, 0, 40, 6, "Catatan", bold=True, font_size=8),
        _field("s_notes", "summary", 0, 6, 110, 12, "delivery.notes", font_size=8),
        _text("s_qty_l", "summary", 112, 0, 28, 6, "Total qty", bold=True, font_size=8, align="right"),
        _field("s_qty", "summary", 142, 0, 38, 6, "delivery.total_qty", align="right", bold=True, font_size=8, number_format="qty"),
        _el(
            "s_sign_recv", "signature", "summary", 0, 22, 56, 28,
            label="Diterima oleh", name="", title="Penerima",
        ),
        _el(
            "s_sign_send", "signature", "summary", 62, 22, 56, 28,
            label="Pengirim", name="", title="Ekspedisi",
        ),
        _el(
            "s_sign_co", "signature", "summary", 124, 22, 56, 28,
            label="Hormat kami", name="Eternal Packaging", title="Bagian Penjualan",
        ),
    ]
    footer = [
        _field("f_page", "page_footer", 0, 1, 180, 6, "page.label", align="center", font_size=8, color="#64748b"),
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "document_type": DOCUMENT_DELIVERY,
        "paper": _paper("A4"),
        "grid": {"enabled": True, "snap": True, "snap_mm": 1},
        "bands": {
            "title": {"height_mm": 16, "elements": title},
            "page_header": {"height_mm": 32, "elements": header},
            "detail": {"height_mm": 8, "elements": [table]},
            "summary": {"height_mm": 52, "elements": summary},
            "page_footer": {"height_mm": 8, "elements": footer},
        },
    }


def builtin_design(document_type: str) -> dict:
    if document_type == DOCUMENT_DELIVERY:
        return builtin_delivery_design()
    return builtin_invoice_design()


def clone_design(design: dict) -> dict:
    return copy.deepcopy(design)


def new_element_id() -> str:
    return "el_" + uuid.uuid4().hex[:10]


def validate_design(design: dict, document_type: Optional[str] = None) -> List[dict]:
    errors: List[dict] = []

    def err(code: str, message: str, element_id: str = ""):
        errors.append({"code": code, "message": message, "element_id": element_id})

    if not isinstance(design, dict):
        err("design", "Desain tidak valid.")
        return errors
    doc_type = document_type or design.get("document_type")
    if doc_type not in DOCUMENT_TYPES:
        err("document_type", "Jenis dokumen tidak didukung.")
        return errors
    if int(design.get("schema_version") or 0) != SCHEMA_VERSION:
        err("schema", "Versi desain tidak didukung.")

    paper = design.get("paper") or {}
    preset = str(paper.get("preset") or "")
    if preset not in PAPER_PRESETS and preset != "custom":
        err("paper", "Ukuran kertas tidak dikenal.")
    width, height = paper_size_mm(design)
    if preset == "custom" or str(paper.get("orientation") or "") == "landscape":
        raw_w = _num(paper.get("width_mm"))
        raw_h = _num(paper.get("height_mm"))
        if preset == "custom" and (raw_w < PAPER_MIN_MM or raw_h < PAPER_MIN_MM or raw_w > PAPER_MAX_MM or raw_h > PAPER_MAX_MM):
            err("paper", f"Ukuran kustom harus {int(PAPER_MIN_MM)}–{int(PAPER_MAX_MM)} mm.")
    if width < PAPER_MIN_MM or height < PAPER_MIN_MM or width > PAPER_MAX_MM or height > PAPER_MAX_MM:
        err("paper", "Ukuran kertas setelah orientasi berada di luar batas.")
    margin = margins_mm(design)
    if any(v < 0 or v > 80 for v in margin.values()):
        err("margin", "Margin tidak valid.")
    inner_w, inner_h = inner_size_mm(design)
    if inner_w < 40 or inner_h < 40:
        err("margin", "Margin tidak menyisakan area cetak yang cukup.")

    bands = design.get("bands") or {}
    if not isinstance(bands, dict):
        err("bands", "Susunan band tidak valid.")
        return errors
    seen_ids = set()
    used_fields = set()
    tables = []
    for band_name in BANDS:
        band = bands.get(band_name)
        if not isinstance(band, dict):
            err("bands", f"Band {band_name} belum ada.")
            continue
        band_h = _num(band.get("height_mm"), -1)
        if band_h <= 0 or band_h > inner_h:
            err("band_height", f"Tinggi band {band_name} tidak valid.", band_name)
        elements = band.get("elements") or []
        if not isinstance(elements, list):
            err("elements", f"Elemen pada band {band_name} tidak valid.")
            continue
        for el in elements:
            if not isinstance(el, dict):
                err("element", "Elemen tidak valid.")
                continue
            eid = str(el.get("id") or "")
            if not eid or eid in seen_ids:
                err("element", "Setiap elemen harus memiliki identitas unik.", eid)
            seen_ids.add(eid)
            if el.get("band") not in (None, band_name):
                err("band", "Band elemen tidak sesuai tempatnya.", eid)
            if el.get("type") not in ELEMENT_TYPES:
                err("element", "Jenis elemen tidak didukung.", eid)
                continue
            x, y = _num(el.get("x_mm"), -1), _num(el.get("y_mm"), -1)
            w, h = _num(el.get("w_mm"), -1), _num(el.get("h_mm"), -1)
            if w <= 0 or h <= 0 or x < -0.05 or y < -0.05 or x + w > inner_w + 0.4 or y + h > band_h + 0.4:
                err("out_of_band", "Ada elemen yang berada di luar band.", eid)
            props = el.get("props") or {}
            font = props.get("font") or "Helvetica"
            if font not in FONTS:
                err("font", "Font tidak tersedia pada renderer.", eid)
            size = _num(props.get("font_size"), 9)
            if props.get("font_size") is not None and (size < 6 or size > 28):
                err("font", "Ukuran font di luar batas 6–28.", eid)
            if props.get("align") and props.get("align") not in ALIGNMENTS:
                err("align", "Perataan teks tidak dikenal.", eid)
            if props.get("italic") and not FONTS.get(font, {}).get("italic_supported", False):
                err("font", "Font ini tidak mendukung italic.", eid)
            if el.get("type") == "field":
                key = str(props.get("field") or "")
                spec = field_spec(doc_type, key)
                if not spec:
                    err("field", "Field tidak tersedia untuk jenis dokumen ini.", eid)
                elif not spec.get("enabled", True):
                    err("field", spec.get("reason") or "Field tidak tersedia.", eid)
                else:
                    used_fields.add(key)
                if props.get("date_format") and props.get("date_format") not in DATE_FORMATS:
                    err("format", "Format tanggal tidak tersedia.", eid)
                if props.get("number_format") and props.get("number_format") not in NUMBER_FORMATS:
                    err("format", "Format angka tidak tersedia.", eid)
            if el.get("type") == "table":
                tables.append(el)
                columns = props.get("columns") or []
                if not isinstance(columns, list) or not columns:
                    err("table", "Struktur tabel tidak valid.", eid)
                else:
                    total_w = 0.0
                    col_fields = set()
                    for col in columns:
                        if not isinstance(col, dict):
                            err("table", "Struktur tabel tidak valid.", eid)
                            continue
                        key = str(col.get("field") or "")
                        spec = field_spec(doc_type, key)
                        cw = _num(col.get("width_mm"), 0)
                        total_w += cw
                        if cw < 8:
                            err("table", "Lebar kolom tabel tidak valid.", eid)
                        if not spec or spec.get("kind") != "line" or not spec.get("enabled", True):
                            err("field", "Kolom memakai field yang tidak tersedia untuk dokumen ini.", eid)
                        else:
                            col_fields.add(key)
                            used_fields.add(key)
                        if col.get("align") and col.get("align") not in ALIGNMENTS:
                            err("align", "Perataan kolom tidak dikenal.", eid)
                    if total_w > w + 0.6:
                        err("table", "Lebar kolom melebihi lebar tabel.", eid)
                    for key in REQUIRED_FIELDS[doc_type]:
                        if key.startswith("line.") and key not in col_fields:
                            err("required", f"Kolom wajib {key} belum ada pada tabel.", eid)
            if el.get("type") == "qr":
                if str(props.get("source") or "tracking") != "tracking":
                    err("qr", "QR hanya boleh memakai URL tracking yang sudah tersedia.", eid)
                used_fields.add("qr.tracking")
            if el.get("type") == "image":
                role = str(props.get("role") or "logo")
                if role not in ("logo", "signature"):
                    err("image", "Gambar hanya boleh logo atau tanda tangan dari aset aplikasi.", eid)
                if props.get("url") or props.get("path") or props.get("src"):
                    err("image", "Gambar dari URL atau path bebas tidak diizinkan.", eid)

    if len(tables) != 1:
        err("table", "Band Detail harus berisi satu tabel rincian.")
    elif tables[0].get("band") not in (None, "detail") and (design.get("bands") or {}).get("detail", {}).get("elements", [None])[0] is not tables[0]:
        err("table", "Tabel rincian harus berada pada band Detail.")

    for key in REQUIRED_FIELDS[doc_type]:
        if key not in used_fields:
            err("required", f"Field wajib belum ada: {key}.")

    title_h = _num((bands.get("title") or {}).get("height_mm"))
    header_h = _num((bands.get("page_header") or {}).get("height_mm"))
    footer_h = _num((bands.get("page_footer") or {}).get("height_mm"))
    summary_h = _num((bands.get("summary") or {}).get("height_mm"))
    detail_h = _num((bands.get("detail") or {}).get("height_mm"))
    if header_h + footer_h + detail_h + 6 > inner_h:
        err("space", "Susunan band tidak menyisakan ruang cetak untuk rincian.")
    if title_h + header_h + footer_h + detail_h > inner_h:
        err("space", "Halaman pertama tidak menyisakan ruang cetak.")
    if header_h + footer_h + summary_h > inner_h:
        err("space", "Ringkasan tidak muat bersama header dan footer.")
    return errors


def sample_invoice(*, lines: Optional[int] = None, long_name: bool = False) -> dict:
    """DATA CONTOH. Not a production document."""
    count = 2 if lines is None else int(lines)
    name = "Paper Cup Eternal 12 oz" if not long_name else (
        "Paper Cup Eternal Packaging cetak full color laminasi food grade ukuran 12 oz untuk minuman dingin"
    )
    rows = []
    for index in range(count):
        qty = 100 + index
        price = 1500
        rows.append({
            "item_name": f"{name} #{index + 1}",
            "quantity": qty,
            "unit": "pcs",
            "unit_price": price,
            "line_subtotal": qty * price,
        })
    subtotal = sum(row["line_subtotal"] for row in rows)
    discount = 5000 if count else 0
    ppn_percent = 11
    taxable = subtotal - discount
    ppn_amount = round(taxable * ppn_percent / 100)
    charges = [{"description": "Ongkos kirim DATA CONTOH", "amount": 15000}]
    charge_total = sum(c["amount"] for c in charges)
    grand = taxable + ppn_amount + charge_total
    paid = 100000 if grand > 100000 else 0
    return {
        "id": "contoh-invoice",
        "invoice_number": "INV/CONTOH/001",
        "invoice_date": "2026-09-04",
        "due_date": "2026-10-04",
        "payment_term_days": 30,
        "po_number": "PO-CONTOH-001",
        "customer": "PT Contoh Pelanggan",
        "address": "Jl. Contoh No. 8, Jakarta",
        "sales": "Sari Contoh",
        "payment_status": "Belum lunas",
        "notes": "DATA CONTOH — catatan faktur",
        "lines": rows,
        "subtotal": subtotal,
        "discount_amount": discount,
        "ppn_percent": ppn_percent,
        "ppn_amount": ppn_amount,
        "other_charges": charges,
        "grand_total": grand,
        "paid_total": paid,
        "outstanding_amount": grand - paid,
        "financial_status": "finalized",
        "_sample": "DATA CONTOH",
    }


def sample_delivery(*, partial: bool = False, lines: Optional[int] = None, long_name: bool = False) -> dict:
    """DATA CONTOH. Qty is the quantity on this delivery, not the whole PO."""
    count = 2 if lines is None else int(lines)
    name = "Paper Bowl Eternal 500 ml" if not long_name else (
        "Paper Bowl Eternal Packaging food grade dengan tutup dan cetak logo pelanggan sepanjang nama komersial"
    )
    rows = []
    for index in range(count):
        po_qty = 100
        sj_qty = 40 if partial else po_qty
        rows.append({
            "name": f"{name} #{index + 1}",
            "qty": sj_qty,
            "quantity": sj_qty,
            "po_quantity": po_qty,
            "unit": "pcs",
            "notes": "DATA CONTOH",
            "item_code": f"PB-{index + 1}",
            "color": "",
            "production_process": "cetak",
        })
    return {
        "id": "contoh-sj",
        "sj_number": "SJ/CONTOH/001",
        "delivery_date": "2026-09-18",
        "po_number": "PO-CONTOH-001",
        "customer": "PT Contoh Pelanggan",
        "recipient": "Budi Penerima",
        "address": "Gudang Contoh, Bekasi",
        "outlet": "Outlet Contoh",
        "sales": "Sari Contoh",
        "expedition": "Armada sendiri",
        "delivery_zone": "Jakarta",
        "shipment_index": 1,
        "notes": "DATA CONTOH — catatan surat jalan",
        "line_items": rows,
        "_sample": "DATA CONTOH",
    }


def _money_of(raw: Any) -> float:
    try:
        return float(raw or 0)
    except (TypeError, ValueError):
        return 0.0


def build_invoice_context(invoice: dict, *, public: bool = False, tracking_token: str = "", preview: bool = False) -> dict:
    """Allowlisted print values. Public mode never reads fields outside the public source."""
    invoice = invoice or {}
    if public:
        # Caller should already have passed customer_invoice_pdf_source.
        # Still ignore anything that is not part of that contract.
        allowed_top = {
            "invoice_number", "po_number", "customer", "address", "sales", "invoice_date",
            "payment_term_days", "due_date", "notes", "payment_status", "lines", "other_charges",
            "subtotal", "discount_amount", "ppn_percent", "ppn_amount", "grand_total",
            "paid_total", "outstanding_amount",
        }
        invoice = {k: invoice.get(k) for k in allowed_top}
        invoice["notes"] = ""
    lines = []
    for index, raw in enumerate(invoice.get("lines") or []):
        if not isinstance(raw, dict):
            continue
        qty = raw.get("quantity") if raw.get("quantity") is not None else raw.get("qty")
        lines.append({
            "line.index": str(index + 1),
            "line.item_name": str(raw.get("item_name") or raw.get("name") or "-"),
            "line.qty": qty if qty is not None else 0,
            "line.unit": str(raw.get("unit") or ""),
            "line.unit_price": raw.get("unit_price") or 0,
            "line.line_subtotal": raw.get("line_subtotal") or 0,
        })
    charges = []
    for raw in invoice.get("other_charges") or []:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or "").strip()
        amount = _money_of(raw.get("amount"))
        if description and amount:
            charges.append({"description": description, "amount": amount})
    grand = _money_of(invoice.get("grand_total"))
    token = str(tracking_token or "").strip()
    tracking_url = f"/tracking/{token}" if token and not preview else ""
    values = {
        "invoice.number": str(invoice.get("invoice_number") or "-"),
        "invoice.date": invoice.get("invoice_date") or "",
        "invoice.customer": str(invoice.get("customer") or "-"),
        "invoice.address": str(invoice.get("address") or "-"),
        "invoice.po_number": str(invoice.get("po_number") or "-"),
        "invoice.payment_term_days": invoice.get("payment_term_days") if invoice.get("payment_term_days") is not None else 0,
        "invoice.due_date": invoice.get("due_date") or "",
        "invoice.sales": str(invoice.get("sales") or "-"),
        "invoice.payment_status": str(invoice.get("payment_status") or "-"),
        "invoice.notes": str(invoice.get("notes") or "-"),
        "invoice.subtotal": invoice.get("subtotal") or 0,
        "invoice.discount_amount": invoice.get("discount_amount") or 0,
        "invoice.ppn_percent": invoice.get("ppn_percent") or 0,
        "invoice.ppn_amount": invoice.get("ppn_amount") or 0,
        "invoice.grand_total": grand,
        "invoice.paid_total": invoice.get("paid_total") or 0,
        "invoice.outstanding_amount": invoice.get("outstanding_amount") or 0,
        "invoice.terbilang": terbilang_rupiah(grand),
        "company.name": COMPANY_IDENTITY["name"],
        "company.tagline": COMPANY_IDENTITY["tagline"],
    }
    return {
        "document_type": DOCUMENT_INVOICE,
        "values": values,
        "lines": lines,
        "charges": charges,
        "discount": _money_of(invoice.get("discount_amount")),
        "ppn_amount": _money_of(invoice.get("ppn_amount")),
        "ppn_percent": invoice.get("ppn_percent") or 0,
        "tracking_url": tracking_url,
        "preview": preview,
        "public": public,
        "sample": bool(invoice.get("_sample")),
    }


def build_delivery_context(delivery: dict, *, tracking_token: str = "", preview: bool = False) -> dict:
    delivery = delivery or {}
    lines = []
    total = 0
    for index, raw in enumerate(delivery.get("line_items") or delivery.get("lines") or []):
        if not isinstance(raw, dict):
            continue
        qty = raw.get("qty") if raw.get("qty") is not None else raw.get("quantity")
        try:
            qty_n = float(qty or 0)
        except (TypeError, ValueError):
            qty_n = 0
        total += qty_n
        process = str(raw.get("production_process") or "")
        process_label = "Non-Cetak" if process.lower() == "non_print" else ("Cetak" if process else "")
        lines.append({
            "line.index": str(index + 1),
            "line.item_name": str(raw.get("name") or raw.get("item_name") or "-"),
            "line.qty": qty if qty is not None else 0,
            "line.unit": str(raw.get("unit") or "pcs"),
            "line.item_code": str(raw.get("item_code") or ""),
            "line.notes": str(raw.get("notes") or ""),
            "line.color": str(raw.get("color") or ""),
            "line.process": process_label,
        })
    token = str(tracking_token or "").strip()
    tracking_url = f"/tracking/{token}" if token and not preview else ""
    values = {
        "delivery.number": str(delivery.get("sj_number") or "-"),
        "delivery.date": delivery.get("delivery_date") or "",
        "delivery.customer": str(delivery.get("customer") or "-"),
        "delivery.recipient": str(delivery.get("recipient") or "-"),
        "delivery.address": str(delivery.get("address") or "-"),
        "delivery.po_number": str(delivery.get("po_number") or "-"),
        "delivery.outlet": str(delivery.get("outlet") or "-"),
        "delivery.notes": str(delivery.get("notes") or "-"),
        "delivery.sales": str(delivery.get("sales") or "-"),
        "delivery.expedition": str(delivery.get("expedition") or "-"),
        "delivery.zone": str(delivery.get("delivery_zone") or "-"),
        "delivery.shipment_index": delivery.get("shipment_index") if delivery.get("shipment_index") is not None else "",
        "delivery.total_qty": total,
        "company.name": COMPANY_IDENTITY["name"],
        "company.tagline": COMPANY_IDENTITY["tagline"],
    }
    return {
        "document_type": DOCUMENT_DELIVERY,
        "values": values,
        "lines": lines,
        "charges": [],
        "discount": 0,
        "ppn_amount": 0,
        "ppn_percent": 0,
        "tracking_url": tracking_url,
        "preview": preview,
        "public": False,
        "sample": bool(delivery.get("_sample")),
    }


def print_cache_token(template: Optional[dict], document: Optional[dict]) -> str:
    """Identity for any future PDF cache. There is no stored PDF cache today."""
    template = template or {}
    document = document or {}
    if template.get("legacy_renderer") or not template:
        version = "legacy"
    else:
        version = str(template.get("active_version") or 0)
    tid = str(template.get("id") or "builtin")
    doc_id = str(document.get("id") or document.get("invoice_number") or document.get("sj_number") or "")
    status = str(document.get("financial_status") or document.get("status") or "")
    updated = str(document.get("updated_at") or "")
    removed = "removed" if document.get("removed_from_active") else "active"
    return f"{tid}:{version}:{doc_id}:{status}:{updated}:{removed}"
