"""Targeted tests for PDF date formatting (dd/mm/yy).

Verifies that all user-visible calendar dates in generated PDFs use dd/mm/yy format,
while document numbers, monetary values, quantities, and PDF layout remain intact.
"""
from datetime import date, datetime
import io
import pytest
import pdfplumber

from services.pdf_date import format_pdf_date
from services.sales_deliveries import generate_sales_delivery_pdf_bytes
from services.sales_invoices import generate_sales_invoice_pdf_bytes
from services.sales_receipts import generate_sales_receipt_pdf_bytes
from services.employee_admin import generate_pdf_bytes as generate_employee_pdf_bytes


def _extract_pdf_text(pdf_bytes: bytes) -> str:
    """Helper to extract all text from generated PDF bytes."""
    text_chunks = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            text_chunks.append(page.extract_text() or "")
    return "\n".join(text_chunks)


# ===========================================================================
# 1. CORE FORMATTER TESTS
# ===========================================================================
def test_format_pdf_date_required_examples():
    """At minimum prove: 2026-09-04 -> 04/09/26, 2026-01-05 -> 05/01/26, 2026-12-31 -> 31/12/26."""
    assert format_pdf_date("2026-09-04") == "04/09/26"
    assert format_pdf_date("2026-01-05") == "05/01/26"
    assert format_pdf_date("2026-12-31") == "31/12/26"


def test_format_pdf_date_source_types():
    """Verify datetime, date, and ISO strings with timezone/time components."""
    assert format_pdf_date(date(2026, 9, 4)) == "04/09/26"
    assert format_pdf_date(datetime(2026, 1, 5, 14, 30, 0)) == "05/01/26"
    assert format_pdf_date("2026-09-04T10:00:00Z") == "04/09/26"
    assert format_pdf_date("2026-12-31T23:59:59+07:00") == "31/12/26"
    assert format_pdf_date("2026-09-04 08:00:00") == "04/09/26"
    assert format_pdf_date("04/09/26") == "04/09/26"


def test_format_pdf_date_invalid_and_empty_safety():
    """Preserve null, empty, and invalid historical values without crashing or inventing dates."""
    assert format_pdf_date(None) == ""
    assert format_pdf_date(None, fallback="-") == "-"
    assert format_pdf_date("") == ""
    assert format_pdf_date("-") == "-"
    assert format_pdf_date("N/A") == "N/A"
    assert format_pdf_date("tidak-ada") == "tidak-ada"


# ===========================================================================
# 2. SURAT JALAN / SALES DELIVERY PDF TESTS
# ===========================================================================
def test_sales_delivery_pdf_date_format():
    """Verify Surat Jalan PDF formats delivery_date as dd/mm/yy and keeps numbers intact."""
    delivery = {
        "id": "DEL-001",
        "sj_number": "SJ/04/09/26/001",
        "po_number": "PO/04/09/26/001",
        "customer": "PT Sumber Rejeki",
        "outlet": "Gudang Pusat",
        "delivery_date": "2026-09-04",
        "recipient": "Pak Joko",
        "sales": "Sales A",
        "address": "Jl. Industri No. 12",
        "expedition": "Armada Sendiri",
        "delivery_zone": "Jabodetabek",
        "line_items": [
            {
                "name": "Box Corrugated 20x20",
                "item_code": "BOX-20",
                "production_process": "print",
                "qty": 5000,
                "unit": "pcs",
                "color": "4 Warna",
                "notes": "Segel aman",
            }
        ],
        "notes": "Pengiriman pagi",
        "created_by_name": "Admin Logistik",
    }

    pdf_bytes = generate_sales_delivery_pdf_bytes(delivery)
    assert pdf_bytes.startswith(b"%PDF-")

    text = _extract_pdf_text(pdf_bytes)
    # Date must be 04/09/26
    assert "04/09/26" in text
    assert "2026-09-04" not in text
    # Document numbers unchanged
    assert "SJ/04/09/26/001" in text
    assert "PO/04/09/26/001" in text
    # Quantity unchanged
    assert "5,000" in text or "5000" in text


# ===========================================================================
# 3. SALES INVOICE (FAKTUR PENJUALAN) PDF TESTS
# ===========================================================================
def test_sales_invoice_pdf_date_format():
    """Verify Sales Invoice PDF formats invoice_date & due_date as dd/mm/yy."""
    invoice = {
        "id": "INV-001",
        "invoice_number": "INV/04/09/26/001",
        "po_number": "PO/04/09/26/001",
        "customer": "PT Pangan Sejahtera",
        "invoice_date": "2026-09-04T00:00:00Z",
        "due_date": "2026-12-31T00:00:00Z",
        "payment_term_days": 118,
        "address": "Kawasan Industri MM2100",
        "notes": "Tempo sesuai kesepakatan",
        "payment_status": "BELUM DIBAYAR",
        "subtotal": 10000000.0,
        "discount_amount": 0,
        "ppn_percent": 11,
        "ppn_amount": 1100000.0,
        "grand_total": 11100000.0,
        "paid_total": 0,
        "outstanding_amount": 11100000.0,
        "lines": [
            {
                "item_name": "Paper Bag Laminated",
                "quantity": 2500,
                "unit": "pcs",
                "unit_price": 4000.0,
                "line_subtotal": 10000000.0,
            }
        ],
    }

    pdf_bytes = generate_sales_invoice_pdf_bytes(invoice)
    assert pdf_bytes.startswith(b"%PDF-")

    text = _extract_pdf_text(pdf_bytes)
    # Dates must be formatted as dd/mm/yy
    assert "04/09/26" in text
    assert "31/12/26" in text
    assert "2026-09-04" not in text
    assert "2026-12-31" not in text
    # Document numbers unchanged
    assert "INV/04/09/26/001" in text
    assert "PO/04/09/26/001" in text
    # Monetary values and quantity unchanged
    assert "11.100.000" in text
    assert "2500" in text or "2.500" in text


# ===========================================================================
# 4. SALES RECEIPT (PENERIMAAN PENJUALAN) PDF TESTS
# ===========================================================================
def test_sales_receipt_pdf_date_format():
    """Verify Sales Receipt PDF formats receipt_date as dd/mm/yy."""
    receipt = {
        "id": "PAY-001",
        "receipt_number": "PAY/05/01/26/001",
        "invoice_number": "INV/04/09/26/001",
        "po_number": "PO/04/09/26/001",
        "customer": "PT Pangan Sejahtera",
        "receipt_date": "2026-01-05T08:00:00Z",
        "payment_state": "PELUNASAN",
        "amount": 11100000.0,
        "notes": "Transfer BCA",
        "created_by_name": "Finance Admin",
    }

    pdf_bytes = generate_sales_receipt_pdf_bytes(receipt)
    assert pdf_bytes.startswith(b"%PDF-")

    text = _extract_pdf_text(pdf_bytes)
    # Date must be 05/01/26
    assert "05/01/26" in text
    assert "2026-01-05" not in text
    # Numbers and money unchanged
    assert "PAY/05/01/26/001" in text
    assert "INV/04/09/26/001" in text
    assert "11.100.000" in text


# ===========================================================================
# 5. EMPLOYEE ADMIN PDF TESTS
# ===========================================================================
def test_employee_admin_pdf_date_format():
    """Verify Employee Admin PDF formats submission date and date form fields as dd/mm/yy."""
    request = {
        "id": "EA-001",
        "request_number": "REQ/04/09/26/001",
        "type_name": "Pengajuan Cuti",
        "submitted_at": "2026-09-04T09:30:00",
        "employee": {
            "name": "Ahmad Dani",
            "employee_id": "EMP-042",
            "role": "Operator Cetak",
            "email": "ahmad@eternalpackaging.com",
        },
        "form_snapshot": {
            "fields": [
                {"key": "tanggal_mulai", "label": "Tanggal Mulai", "type": "date", "active": True},
                {"key": "tanggal_selesai", "label": "Tanggal Selesai", "type": "date", "active": True},
            ]
        },
        "values": {
            "tanggal_mulai": "2026-09-04",
            "tanggal_selesai": "2026-12-31",
        },
        "approval_history": [
            {
                "action": "approved",
                "level": 1,
                "actor": {"name": "Supervisor"},
                "timestamp": "2026-09-04T10:00:00",
                "note": "Disetujui",
            }
        ],
        "document": {
            "document_number": "DOC/04/09/26/001",
            "template_snapshot": {
                "title": "Surat Keterangan Cuti",
                "header_text": "ETERNAL PACKAGING",
                "footer_text": "Dokumen Sah",
                "sections": [{"heading": "Rincian", "body": "{{submitted_fields}}"}],
            },
        },
    }

    pdf_bytes = generate_employee_pdf_bytes(request)
    assert pdf_bytes.startswith(b"%PDF-")

    text = _extract_pdf_text(pdf_bytes)
    # Header Tanggal must be 04/09/26
    assert "04/09/26" in text
    assert "31/12/26" in text
    assert "2026-09-04" not in text
    assert "2026-12-31" not in text
    # Document number unchanged
    assert "DOC/04/09/26/001" in text
