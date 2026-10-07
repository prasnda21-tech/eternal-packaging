"""Phase 3 generic PO header/footer — Design Cetakan."""
from __future__ import annotations

import os
from io import BytesIO
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_po_test")

from services.order_pdf import generate_sales_order_pdf_bytes  # noqa: E402
from services.print_design_settings import (  # noqa: E402
    _sanitize_footer_elements,
    _sanitize_header_elements,
    resolve_print_design,
)
from services.print_field_registry import (  # noqa: E402
    SOURCE_DOCUMENT_DUE_DATE,
    SOURCE_PO_SIGNATURES_BLOCK,
    default_po_footer_elements,
    default_po_header_elements,
)
from services.print_footer_render import footer_elements_active  # noqa: E402
from services.print_header_render import header_elements_active  # noqa: E402


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _order():
    return {
        "po_number": "PO/P3/001",
        "order_date": "2026-03-01",
        "customer": "PT PO Test",
        "address": "Jl. PO 1",
        "payment_terms": "Tempo 14",
        "created_by_user": "User A",
        "expedition": "JNE",
        "delivery_zone": "Dalam Kota",
        "sales": "Sales P",
        "estimated_delivery": "2026-03-10",
        "notes": "Catatan PO body",
        "line_items": [
            {"item_code": "P1", "name": "Item PO", "quantity": 3, "unit": "pcs", "color": "Merah"},
        ],
        "total_quantity": 3,
    }


def test_po_legacy_header_footer():
    design = resolve_print_design(None)
    assert design["po"]["header_elements"] == []
    assert design["po"]["footer_elements"] == []
    text = _pdf_text(generate_sales_order_pdf_bytes(_order(), design=design))
    assert "PT PO Test" in text
    assert "PO/P3/001" in text
    assert "Operator" in text
    assert "Catatan PO body" in text


def test_po_generic_header_legacy_footer():
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "po": {"header_elements": default_po_header_elements()[:4]},
    })
    assert design["schema_version"] == 2
    assert header_elements_active(design["po"]["header_elements"])
    assert not footer_elements_active(design["po"]["footer_elements"])
    text = _pdf_text(generate_sales_order_pdf_bytes(_order(), design=design))
    assert "ETERNAL PACKAGING" in text
    assert "PT PO Test" in text
    assert "Operator" in text


def test_po_legacy_header_generic_footer():
    design = resolve_print_design({
        "po": {
            "footer_elements": default_po_footer_elements(),
            "signature_labels": ["Op A", "Kirim B", "Terima C"],
            "footer_name_label": "Nama",
        },
    })
    assert footer_elements_active(design["po"]["footer_elements"])
    text = _pdf_text(generate_sales_order_pdf_bytes(_order(), design=design))
    assert "Op A" in text
    assert "Kirim B" in text
    assert "Item PO" in text


def test_po_generic_both():
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "po": {
            "header_elements": default_po_header_elements()[:3],
            "footer_elements": default_po_footer_elements(),
        },
    })
    text = _pdf_text(generate_sales_order_pdf_bytes(_order(), design=design))
    assert "ETERNAL PACKAGING" in text
    assert "Item PO" in text


def test_po_rejects_invoice_only_source():
    with pytest.raises(HTTPException):
        _sanitize_header_elements(
            [{"id": "x", "type": "field", "source": SOURCE_DOCUMENT_DUE_DATE, "align": "left", "visible": True}],
            strict=True,
            document="po",
        )


def test_po_body_unchanged_with_generic():
    design = resolve_print_design({"po": {"header_elements": default_po_header_elements()[:2]}})
    text = _pdf_text(generate_sales_order_pdf_bytes(_order(), design=design))
    assert "Item PO" in text
    assert "Catatan PO body" in text
    assert "Total Kuantitas" in text


def test_po_signatures_block_allowed_in_footer():
    rows = _sanitize_footer_elements(
        [{"id": "s", "type": "field", "source": SOURCE_PO_SIGNATURES_BLOCK, "align": "center", "visible": True}],
        strict=True,
        document="po",
    )
    assert rows[0]["source"] == SOURCE_PO_SIGNATURES_BLOCK
