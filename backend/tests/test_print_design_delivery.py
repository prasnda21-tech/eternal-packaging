"""Phase 3 generic Surat Jalan header/footer — Design Cetakan."""
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

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_sj_test")

from services.print_design_settings import (  # noqa: E402
    _sanitize_footer_elements,
    _sanitize_header_elements,
    resolve_print_design,
)
from services.print_field_registry import (  # noqa: E402
    SOURCE_DOCUMENT_PAYMENT_TERMS,
    SOURCE_SIGNATURE_PREPARED,
    default_delivery_footer_elements,
    default_delivery_header_elements,
)
from services.print_footer_render import footer_elements_active  # noqa: E402
from services.print_header_render import header_elements_active  # noqa: E402
from services.sales_deliveries import generate_sales_delivery_pdf_bytes  # noqa: E402


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        return "\n".join((p.extract_text() or "") for p in pdf.pages)


def _delivery():
    return {
        "sj_number": "SJ/P3/001",
        "delivery_date": "2026-03-05",
        "customer": "PT SJ Test",
        "address": "Jl. SJ 1",
        "outlet": "Outlet S",
        "sales": "Sales S",
        "expedition": "JNT",
        "delivery_zone": "Luar Kota",
        "created_by_name": "Admin SJ",
        "po_number": "PO/REF/1",
        "notes": "Catatan SJ",
        "line_items": [
            {"item_code": "S1", "name": "Item SJ", "qty": 2, "unit": "pcs", "outlet": "Outlet S"},
        ],
        "total_qty": 2,
    }


def test_sj_legacy_header_footer():
    design = resolve_print_design(None)
    assert design["surat_jalan"]["header_elements"] == []
    assert design["surat_jalan"]["footer_elements"] == []
    text = _pdf_text(generate_sales_delivery_pdf_bytes(_delivery(), design=design))
    assert "SURAT JALAN" in text
    assert "PT SJ Test" in text
    assert "Gudang" in text
    assert "Kurir" in text
    assert "Penerima" in text
    assert "Item SJ" in text


def test_sj_generic_header_legacy_footer_keeps_pod_labels():
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "surat_jalan": {"header_elements": default_delivery_header_elements()[:4]},
    })
    assert header_elements_active(design["surat_jalan"]["header_elements"])
    assert not footer_elements_active(design["surat_jalan"]["footer_elements"])
    text = _pdf_text(generate_sales_delivery_pdf_bytes(_delivery(), design=design))
    assert "ETERNAL PACKAGING" in text
    assert "Gudang" in text
    assert "Kurir" in text


def test_sj_legacy_header_generic_footer_still_keeps_pod():
    design = resolve_print_design({
        "surat_jalan": {"footer_elements": default_delivery_footer_elements()},
    })
    assert footer_elements_active(design["surat_jalan"]["footer_elements"])
    text = _pdf_text(generate_sales_delivery_pdf_bytes(_delivery(), design=design))
    assert "Catatan SJ" in text
    assert "Gudang" in text
    assert "Penerima" in text
    assert "Item SJ" in text


def test_sj_generic_both():
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "surat_jalan": {
            "header_elements": default_delivery_header_elements()[:3],
            "footer_elements": default_delivery_footer_elements(),
        },
    })
    text = _pdf_text(generate_sales_delivery_pdf_bytes(_delivery(), design=design))
    assert "ETERNAL PACKAGING" in text
    assert "SURAT JALAN" in text
    assert "Gudang" in text


def test_sj_rejects_po_only_source():
    with pytest.raises(HTTPException):
        _sanitize_header_elements(
            [{"id": "x", "type": "field", "source": SOURCE_DOCUMENT_PAYMENT_TERMS, "align": "left", "visible": True}],
            strict=True,
            document="delivery",
        )


def test_sj_rejects_invoice_signature_type():
    with pytest.raises(HTTPException):
        _sanitize_footer_elements(
            [{
                "id": "s",
                "type": "signature",
                "source": SOURCE_SIGNATURE_PREPARED,
                "align": "left",
                "visible": True,
            }],
            strict=True,
            document="delivery",
        )


def test_sj_body_unchanged():
    design = resolve_print_design({
        "surat_jalan": {"header_elements": default_delivery_header_elements()[:2]},
    })
    text = _pdf_text(generate_sales_delivery_pdf_bytes(_delivery(), design=design))
    assert "Item SJ" in text
    assert "Total Kuantitas" in text
