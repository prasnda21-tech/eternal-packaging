import pytest
from services.sales_invoices import generate_sales_invoice_pdf_bytes
from services.sales_receipts import generate_sales_receipt_pdf_bytes

def test_invoice_pdf_generator_success():
    invoice = {
        "id": "INV-TEST-001",
        "invoice_number": "INV-TEST-001",
        "po_number": "PO-001",
        "customer": "Budi",
        "invoice_date": "2026-09-02T10:00:00Z",
        "due_date": "2026-09-12T10:00:00Z",
        "address": "Jalan Kemerdekaan \u2605", # Unicode support test
        "payment_term_days": 10,
        "notes": "Null notes handled?",
        "payment_status": "BELUM DIBAYAR",
        "subtotal": 150000,
        "discount_amount": 10000,
        "ppn_percent": 11,
        "ppn_amount": 15400,
        "grand_total": 155400,
        "paid_total": 0,
        "outstanding_amount": 155400,
        "lines": [
            {
                "item_name": "Kotak Nasi",
                "quantity": 100,
                "unit": "pcs",
                "unit_price": 1000,
                "line_subtotal": 100000
            },
            {
                "item_name": "Kotak Nasi", # Duplicate same name to ensure no collapse
                "quantity": 50,
                "unit": "pcs",
                "unit_price": 1000,
                "line_subtotal": 50000
            }
        ]
    }

    pdf_bytes = generate_sales_invoice_pdf_bytes(invoice)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b'%PDF-')
    assert len(pdf_bytes) > 1000

def test_invoice_pdf_generator_nullable_fields():
    invoice = {
        "id": "INV-TEST-002",
        "lines": None,
        "subtotal": None,
        "notes": None
    }
    pdf_bytes = generate_sales_invoice_pdf_bytes(invoice)
    assert pdf_bytes.startswith(b'%PDF-')

def test_receipt_pdf_generator_success():
    receipt = {
        "id": "PAY-TEST-001",
        "receipt_number": "PAY-TEST-001",
        "invoice_number": "INV-TEST-001",
        "po_number": "PO-TEST-001",
        "customer": "Toko Sejahtera \u263A",
        "receipt_date": "2026-09-02T10:00:00Z",
        "payment_state": "DP",
        "amount": 50000.0,
        "notes": "Transfer Bank ABC"
    }
    pdf_bytes = generate_sales_receipt_pdf_bytes(receipt)
    assert isinstance(pdf_bytes, bytes)
    assert pdf_bytes.startswith(b'%PDF-')
    assert len(pdf_bytes) > 1000

def test_receipt_pdf_generator_nullable_fields():
    receipt = {
        "id": "PAY-TEST-002"
    }
    pdf_bytes = generate_sales_receipt_pdf_bytes(receipt)
    assert pdf_bytes.startswith(b'%PDF-')


def test_invoice_duplicate_lines_structure(monkeypatch):
    import reportlab.platypus
    original_table = reportlab.platypus.Table
    table_calls = []

    def mock_table(data, *args, **kwargs):
        table_calls.append(data)
        return original_table(data, *args, **kwargs)

    monkeypatch.setattr(reportlab.platypus, 'Table', mock_table)

    invoice = {
        "id": "INV-TEST-003",
        "lines": [
            {"item_name": "Produk <A>", "quantity": 10, "unit": "pcs"},
            {"item_name": "Produk <A>", "quantity": 10, "unit": "pcs"},
        ]
    }
    generate_sales_invoice_pdf_bytes(invoice)

    # The items table is the one whose header starts with Kode Barang.
    items_table_data = next(
        data for data in table_calls
        if data and getattr(data[0][0], "text", "").find("Kode Barang") >= 0
    )
    assert len(items_table_data) == 3 # Header + 2 data rows
    assert "Produk &lt;A&gt;" in items_table_data[1][1].text # Escaped
    assert "Produk &lt;A&gt;" in items_table_data[2][1].text

def test_receipt_escaping():
    receipt = {
        "id": "PAY-TEST-003",
        "notes": "Transfer > 500 & cash"
    }
    pdf_bytes = generate_sales_receipt_pdf_bytes(receipt)
    assert b"%PDF-" in pdf_bytes
