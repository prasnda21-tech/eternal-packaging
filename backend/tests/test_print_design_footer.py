"""Phase 2 generic invoice/proforma footer — Design Cetakan."""
from __future__ import annotations

import copy
import os
from io import BytesIO
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_footer_test")

from services.print_design_settings import (  # noqa: E402
    _sanitize_footer_elements,
    _validate_put_payload,
    default_settings,
    resolve_print_design,
    save_print_design_settings,
)
from services.print_field_registry import (  # noqa: E402
    SOURCE_COMPANY_NAME,
    SOURCE_SIGNATURE_APPROVED,
    SOURCE_SIGNATURE_PREPARED,
    SOURCE_STATIC_TEXT,
    default_invoice_footer_elements,
    default_invoice_header_elements,
)
from services.print_footer_render import footer_elements_active  # noqa: E402
from services.print_header_render import header_elements_active  # noqa: E402
from services.sales_invoices import DOCUMENT_PROFORMA, generate_sales_invoice_pdf_bytes  # noqa: E402


class MockCollection:
    def __init__(self, initial=None):
        self._docs = copy.deepcopy(initial or [])

    async def find_one(self, query=None, projection=None):
        query = query or {}
        for doc in self._docs:
            if all(doc.get(k) == v for k, v in query.items() if not str(k).startswith("$")):
                res = copy.deepcopy(doc)
                if projection and projection.get("_id") == 0 and "_id" in res:
                    del res["_id"]
                return res
        return None

    async def update_one(self, query, update, upsert=False):
        set_vals = update.get("$set", {})
        for idx, doc in enumerate(self._docs):
            if all(doc.get(k) == v for k, v in query.items() if not str(k).startswith("$")):
                self._docs[idx].update(copy.deepcopy(set_vals))
                return
        if upsert:
            new_doc = copy.deepcopy(query)
            new_doc.update(copy.deepcopy(set_vals))
            self._docs.append(new_doc)


class MockDb:
    def __init__(self, app_settings=None):
        self.app_settings = MockCollection(app_settings)


def _pdf_text(pdf_bytes: bytes) -> str:
    import pdfplumber

    pages = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def _invoice():
    return {
        "invoice_number": "INV/FTR/001",
        "invoice_date": "2026-03-01",
        "due_date": "2026-03-15",
        "customer": "PT Footer Test",
        "address": "Jl. Footer 1",
        "outlet": "Outlet F",
        "sales": "Sales F",
        "po_number": "PO-F",
        "lines": [
            {
                "item_code": "B1",
                "item_name": "Item B",
                "quantity": 5,
                "unit_price": 2000,
                "line_subtotal": 10000,
            }
        ],
        "subtotal": 10000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "paid_total": 0,
        "grand_total": 10000,
        "notes": "Catatan body footer",
    }


def test_legacy_header_and_footer_unchanged():
    design = resolve_print_design(None)
    assert design["schema_version"] == 1
    assert design["invoice"]["header_elements"] == []
    assert design["invoice"]["footer_elements"] == []
    assert footer_elements_active([]) is False
    pdf = generate_sales_invoice_pdf_bytes(_invoice(), design=design)
    text = _pdf_text(pdf)
    assert "Faktur Penjualan" in text
    assert "Disiapkan Oleh" in text
    assert "Disetujui Oleh" in text
    assert "Catatan body footer" in text
    assert "10.000" in text or "10000" in text


def test_generic_header_legacy_footer():
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "invoice": {"header_elements": default_invoice_header_elements()[:2]},
    })
    assert design["schema_version"] == 2
    assert header_elements_active(design["invoice"]["header_elements"])
    assert not footer_elements_active(design["invoice"]["footer_elements"])
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "ETERNAL PACKAGING" in text
    assert "Disiapkan Oleh" in text
    assert "Catatan body footer" in text


def test_legacy_header_generic_footer():
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "invoice": {
            "footer_elements": default_invoice_footer_elements(),
            "prepared_label": "Disiapkan Oleh",
            "prepared_footer": "Admin F",
            "approved_label": "Disetujui Oleh",
            "approved_footer": "Pengelola F",
            "estimasi_title": "Estimasi Ftr",
            "estimasi_lines": ["Line A", "Line B"],
        },
    })
    assert design["schema_version"] == 2
    assert not header_elements_active(design["invoice"]["header_elements"])
    assert footer_elements_active(design["invoice"]["footer_elements"])
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "Faktur Penjualan" in text
    assert "Disiapkan Oleh" in text
    assert "Admin F" in text
    assert "Estimasi Ftr" in text
    assert "Line A" in text
    assert "Catatan body footer" in text


def test_generic_header_and_footer():
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "invoice": {
            "header_elements": default_invoice_header_elements()[:2],
            "footer_elements": default_invoice_footer_elements(),
            "estimasi_title": "Est Combo",
            "estimasi_lines": ["Combo 1"],
        },
    })
    assert design["schema_version"] == 2
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "ETERNAL PACKAGING" in text
    assert "Est Combo" in text
    assert "Catatan body footer" in text


def test_generic_footer_invoice_and_proforma():
    design = resolve_print_design({
        "invoice": {
            "footer_elements": default_invoice_footer_elements(),
            "estimasi_title": "Est PDF",
            "estimasi_lines": ["E1"],
        },
    })
    inv_text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "Faktur Penjualan" in inv_text
    assert "Est PDF" in inv_text
    pro_text = _pdf_text(
        generate_sales_invoice_pdf_bytes(_invoice(), document_type=DOCUMENT_PROFORMA, design=design)
    )
    assert "PROFORMA INVOICE" in pro_text
    assert "Est PDF" in pro_text
    assert "Catatan body footer" in pro_text


def test_unknown_footer_source_rejected():
    with pytest.raises(HTTPException) as exc:
        _sanitize_footer_elements(
            [{"id": "x", "type": "field", "source": "hpp.margin", "align": "left", "visible": True}],
            strict=True,
        )
    assert exc.value.status_code == 400


def test_malformed_footer_elements_rejected():
    with pytest.raises(HTTPException):
        _validate_put_payload({
            "company_name": "ETERNAL PACKAGING",
            "invoice": {"footer_elements": "bad"},
        })


def test_static_text_html_rejected_and_escaped_when_soft():
    with pytest.raises(HTTPException):
        _sanitize_footer_elements(
            [{
                "id": "s",
                "type": "static_text",
                "source": SOURCE_STATIC_TEXT,
                "align": "left",
                "visible": True,
                "text": "<b>x</b>",
            }],
            strict=True,
        )
    soft = _sanitize_footer_elements(
        [{
            "id": "s",
            "type": "static_text",
            "source": SOURCE_STATIC_TEXT,
            "align": "left",
            "visible": True,
            "text": "SAFE_STATIC",
        }],
        strict=False,
    )
    design = resolve_print_design({"invoice": {"footer_elements": soft}})
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "SAFE_STATIC" in text


def test_signature_sources_use_design_labels():
    # Minimal valid PNG (1x1) so signature_image_flowable can load prepared asset.
    png_1x1 = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    elements = [
        {
            "id": "p",
            "type": "signature",
            "source": SOURCE_SIGNATURE_PREPARED,
            "label": "",
            "align": "left",
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "a",
            "type": "signature",
            "source": SOURCE_SIGNATURE_APPROVED,
            "label": "",
            "align": "center",
            "bold": False,
            "visible": True,
            "text": "",
        },
    ]
    design = resolve_print_design({
        "invoice": {
            "footer_elements": elements,
            "prepared_label": "Prep Label",
            "prepared_footer": "Prep Footer",
            "approved_label": "Appr Label",
            "approved_footer": "Appr Footer",
        },
        "_pdf_assets": {
            "prepared_signature_image": png_1x1,
            "approved_signature_image": None,
        },
    })
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "Prep Label" in text
    assert "Prep Footer" in text
    assert "Appr Label" in text
    assert "Appr Footer" in text


def test_hidden_footer_element_not_rendered_order_align():
    elements = [
        {
            "id": "a",
            "type": "field",
            "source": SOURCE_COMPANY_NAME,
            "label": "",
            "align": "left",
            "bold": True,
            "visible": True,
            "text": "",
        },
        {
            "id": "b",
            "type": "static_text",
            "source": SOURCE_STATIC_TEXT,
            "label": "",
            "align": "right",
            "bold": False,
            "visible": False,
            "text": "HIDDEN_FTR",
        },
        {
            "id": "c",
            "type": "static_text",
            "source": SOURCE_STATIC_TEXT,
            "label": "",
            "align": "right",
            "bold": False,
            "visible": True,
            "text": "VISIBLE_FTR",
        },
    ]
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "invoice": {"footer_elements": elements},
    })
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "HIDDEN_FTR" not in text
    assert "VISIBLE_FTR" in text
    assert "ETERNAL PACKAGING" in text


def test_body_totals_untouched_with_generic_footer():
    design = resolve_print_design({
        "invoice": {"footer_elements": default_invoice_footer_elements()},
    })
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "Item B" in text
    assert "Sub Total" in text or "Subtotal" in text or "10.000" in text
    assert "Catatan body footer" in text


@pytest.mark.anyio
async def test_save_persists_footer_elements():
    db = MockDb()
    elements = default_invoice_footer_elements()
    payload = {
        "company_name": "ETERNAL PACKAGING",
        "address_lines": ["Alamat 1"],
        "accent_color": "#cc1f1a",
        "invoice": {"footer_elements": elements},
    }
    saved = await save_print_design_settings(db, payload, user={"email": "a@b.c", "name": "A"})
    assert saved["schema_version"] == 2
    assert len(saved["invoice"]["footer_elements"]) == 3
    again = resolve_print_design(db.app_settings._docs[0])
    assert again["schema_version"] == 2
    assert again["invoice"]["footer_elements"][0]["source"] == SOURCE_SIGNATURE_PREPARED


def test_defaults_include_empty_footer():
    d = default_settings()
    assert d["invoice"]["footer_elements"] == []
