"""Shared pieces for Eternal sales PDFs. Presentation only."""
from __future__ import annotations

import io
from html import escape
from pathlib import Path
from typing import Iterable, List, Optional, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate

# Owner reference letterhead. Not a settings document.
COMPANY_NAME = "ETERNAL PACKAGING"
COMPANY_ADDRESS_LINES = (
    "Dusun Maron, Donoharjo, Kec. Ngaglik",
    "Kab. Sleman D.I. Yogyakarta 55581",
    "Indonesia",
)

# Owner reference invoice accounts. Not cash-bank master data.
BANK_LINES = (
    "BRI : 024701002548569",
    "A.N : DIMAS AJI PRASETYA",
    "BCA : 8467072051",
    "A.N : SISWADI",
)

# Owner reference wording. Not delivery-deadline settings.
ESTIMASI_TITLE = "INFO ESTIMASI PENGERJAAN"
ESTIMASI_LINES = (
    "* Untuk sablon 1 warna estimasi 1 - 3 hari (+1 hari pengiriman)",
    "* Untuk sablon 1-4 warna : 2 - 5 hari (+1 hari pengiriman)",
    "* Estimasi hari terhitung sejak ACC DESIGN dan PA",
)

ACCENT_COLOR_HEX = "#cc1f1a"
RED = colors.HexColor(ACCENT_COLOR_HEX)
RULE = colors.black

PAGE_MARGIN_X = 11 * mm
PAGE_MARGIN_TOP = 12 * mm
PAGE_MARGIN_BOTTOM = 16 * mm


def content_width(pagesize=A4) -> float:
    return pagesize[0] - (2 * PAGE_MARGIN_X)


def logo_path() -> Optional[str]:
    """Existing web wordmark. No new logo file."""
    root = Path(__file__).resolve().parents[2]
    candidate = root / "frontend" / "src" / "assets" / "branding" / "eternal-logo-full-white.png"
    if candidate.is_file():
        return str(candidate)
    return None


def esc(value) -> str:
    text = "" if value is None else str(value)
    return escape(text).replace("\n", "<br/>")


def format_qty(value) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "" if value is None else str(value)
    if number.is_integer():
        return f"{int(number):,}".replace(",", ".")
    return str(value)


def format_money(value) -> str:
    """Indonesian thousands separator. Same Rp prefix the existing PDFs used."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return "Rp 0"
    if number.is_integer():
        body = f"{int(number):,}".replace(",", ".")
    else:
        body = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    sign = "-" if number < 0 else ""
    return f"{sign}Rp {body[1:] if number < 0 else body}"


_ONES = (
    "",
    "satu",
    "dua",
    "tiga",
    "empat",
    "lima",
    "enam",
    "tujuh",
    "delapan",
    "sembilan",
    "sepuluh",
    "sebelas",
)


def _terbilang_int(number: int) -> str:
    if number < 0:
        return "minus " + _terbilang_int(-number)
    if number < 12:
        return _ONES[number] or "nol"
    if number < 20:
        return _ONES[number - 10] + " belas"
    if number < 100:
        head = _ONES[number // 10] + " puluh"
        tail = number % 10
        return head if tail == 0 else f"{head} {_ONES[tail]}"
    if number < 200:
        rest = number - 100
        return "seratus" if rest == 0 else "seratus " + _terbilang_int(rest)
    if number < 1000:
        head = _ONES[number // 100] + " ratus"
        rest = number % 100
        return head if rest == 0 else f"{head} {_terbilang_int(rest)}"
    if number < 2000:
        rest = number - 1000
        return "seribu" if rest == 0 else "seribu " + _terbilang_int(rest)
    if number < 1_000_000:
        head = _terbilang_int(number // 1000) + " ribu"
        rest = number % 1000
        return head if rest == 0 else f"{head} {_terbilang_int(rest)}"
    if number < 1_000_000_000:
        head = _terbilang_int(number // 1_000_000) + " juta"
        rest = number % 1_000_000
        return head if rest == 0 else f"{head} {_terbilang_int(rest)}"
    if number < 1_000_000_000_000:
        head = _terbilang_int(number // 1_000_000_000) + " miliar"
        rest = number % 1_000_000_000
        return head if rest == 0 else f"{head} {_terbilang_int(rest)}"
    return str(number)


def terbilang(value) -> str:
    """Rupiah amount in Indonesian words. Display only."""
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        return ""
    sign = "minus " if number < 0 else ""
    whole = int(abs(number))
    words = sign + _terbilang_int(whole)
    fraction = abs(number) - whole
    if fraction > 0.0000001:
        cents = int(round(fraction * 100))
        if cents:
            words = f"{words} koma {_terbilang_int(cents)}"
    if not words:
        return ""
    return words[0].upper() + words[1:]


def accent_color(hex_value: Optional[str] = None):
    """ReportLab color from settings hex; invalid/empty falls back to default red."""
    raw = str(hex_value or "").strip() or ACCENT_COLOR_HEX
    try:
        return colors.HexColor(raw)
    except Exception:
        return RED


def styles(accent_hex: Optional[str] = None):
    base = getSampleStyleSheet()
    accent = accent_color(accent_hex)
    normal = ParagraphStyle(
        "EternalBody",
        parent=base["Normal"],
        fontName="Helvetica",
        fontSize=8,
        leading=10,
        textColor=colors.black,
    )
    return {
        "body": normal,
        "bold": ParagraphStyle("EternalBold", parent=normal, fontName="Helvetica-Bold"),
        "small": ParagraphStyle("EternalSmall", parent=normal, fontSize=7.5, leading=9),
        "company": ParagraphStyle(
            "EternalCompany",
            parent=normal,
            fontName="Helvetica-Bold",
            fontSize=12,
            leading=14,
        ),
        "address": ParagraphStyle("EternalAddress", parent=normal, fontSize=8, leading=10),
        "title": ParagraphStyle(
            "EternalTitle",
            parent=normal,
            fontName="Helvetica-Bold",
            fontSize=13,
            leading=16,
            alignment=TA_CENTER,
        ),
        "center": ParagraphStyle("EternalCenter", parent=normal, alignment=TA_CENTER),
        "center_bold": ParagraphStyle(
            "EternalCenterBold",
            parent=normal,
            fontName="Helvetica-Bold",
            alignment=TA_CENTER,
        ),
        "right": ParagraphStyle("EternalRight", parent=normal, alignment=TA_RIGHT),
        "right_bold": ParagraphStyle(
            "EternalRightBold",
            parent=normal,
            fontName="Helvetica-Bold",
            alignment=TA_RIGHT,
        ),
        "red": ParagraphStyle(
            "EternalRed",
            parent=normal,
            textColor=accent,
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
        ),
        "red_small": ParagraphStyle(
            "EternalRedSmall",
            parent=normal,
            textColor=accent,
            fontName="Helvetica",
            fontSize=7,
            leading=9,
        ),
        "warn": ParagraphStyle(
            "EternalWarn",
            parent=normal,
            fontName="Helvetica-Oblique",
            fontSize=7.5,
            leading=9,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#7e22ce"),
        ),
        "header": ParagraphStyle(
            "EternalHeader",
            parent=normal,
            fontName="Helvetica-Bold",
            fontSize=8,
            leading=10,
            alignment=TA_CENTER,
        ),
        "accent": accent,
    }


class NumberedCanvas(canvas.Canvas):
    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self._draw_page_number(page_count)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def _draw_page_number(self, page_count: int) -> None:
        page = self._pageNumber
        label = f"Halaman {page} dari {page_count}"
        self.setFont("Helvetica", 8)
        self.drawRightString(self._pagesize[0] - PAGE_MARGIN_X, 8 * mm, label)


def build_pdf(elements: Sequence, pagesize=A4) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=pagesize,
        leftMargin=PAGE_MARGIN_X,
        rightMargin=PAGE_MARGIN_X,
        topMargin=PAGE_MARGIN_TOP,
        bottomMargin=PAGE_MARGIN_BOTTOM,
    )
    doc.build(list(elements), canvasmaker=NumberedCanvas)
    return buffer.getvalue()


def scale_widths(weights: Iterable[float], total: float) -> List[float]:
    weights = list(weights)
    raw = sum(weights) or 1
    return [total * (weight / raw) for weight in weights]
