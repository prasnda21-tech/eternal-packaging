"""Phase 1 generic invoice/proforma header — Design Cetakan."""
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

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_header_test")

from services.print_design_settings import (  # noqa: E402
    _sanitize_header_elements,
    _validate_put_payload,
    default_settings,
    resolve_print_design,
    save_print_design_settings,
)
from services.print_field_registry import (  # noqa: E402
    SOURCE_COMPANY_NAME,
    SOURCE_STATIC_TEXT,
    default_invoice_header_elements,
)
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
        "invoice_number": "INV/HDR/001",
        "invoice_date": "2026-03-01",
        "due_date": "2026-03-15",
        "customer": "PT Header Test",
        "address": "Jl. Header 1",
        "outlet": "Outlet A",
        "sales": "Sales A",
        "po_number": "PO-9",
        "lines": [
            {
                "item_code": "A1",
                "item_name": "Item A",
                "quantity": 10,
                "unit_price": 1000,
                "line_subtotal": 10000,
            }
        ],
        "subtotal": 10000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "paid_total": 0,
        "grand_total": 10000,
        "notes": "Catatan body",
    }


def test_v1_empty_header_elements_uses_legacy_path():
    design = resolve_print_design(None)
    assert design["schema_version"] == 1
    assert design["invoice"]["header_elements"] == []
    assert header_elements_active(design["invoice"]["header_elements"]) is False
    pdf = generate_sales_invoice_pdf_bytes(_invoice(), design=design)
    text = _pdf_text(pdf)
    assert "Faktur Penjualan" in text
    assert "PT Header Test" in text
    assert "INV/HDR/001" in text
    assert "Catatan body" in text
    assert "10.000" in text or "10000" in text or "10.000" in text.replace(".", "")


def test_v1_proforma_unchanged_without_header_elements():
    design = resolve_print_design({})
    pdf = generate_sales_invoice_pdf_bytes(_invoice(), document_type=DOCUMENT_PROFORMA, design=design)
    text = _pdf_text(pdf)
    assert "PROFORMA INVOICE" in text
    assert "PT Header Test" in text


def test_unknown_source_rejected():
    with pytest.raises(HTTPException) as exc:
        _sanitize_header_elements(
            [{"id": "x", "type": "field", "source": "hpp.margin", "align": "left", "visible": True}],
            strict=True,
        )
    assert exc.value.status_code == 400


def test_malformed_header_elements_rejected():
    with pytest.raises(HTTPException):
        _validate_put_payload({
            "company_name": "ETERNAL PACKAGING",
            "invoice": {"header_elements": "not-a-list"},
        })


def test_html_static_text_rejected():
    with pytest.raises(HTTPException):
        _sanitize_header_elements(
            [{
                "id": "s",
                "type": "static_text",
                "source": SOURCE_STATIC_TEXT,
                "align": "left",
                "visible": True,
                "text": "<script>x</script>",
            }],
            strict=True,
        )


def test_v2_generic_header_invoice_and_proforma():
    elements = default_invoice_header_elements()
    design = resolve_print_design({
        "company_name": "ETERNAL PACKAGING",
        "address_lines": ["Alamat 1"],
        "invoice": {"header_elements": elements},
    })
    assert design["schema_version"] == 2
    assert header_elements_active(design["invoice"]["header_elements"]) is True

    inv_text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "Faktur Penjualan" in inv_text
    assert "ETERNAL PACKAGING" in inv_text
    assert "PT Header Test" in inv_text
    assert "INV/HDR/001" in inv_text
    assert "Sales A" in inv_text
    assert "Catatan body" in inv_text  # body untouched

    pro_text = _pdf_text(
        generate_sales_invoice_pdf_bytes(_invoice(), document_type=DOCUMENT_PROFORMA, design=design)
    )
    assert "PROFORMA INVOICE" in pro_text
    assert "PT Header Test" in pro_text


def test_hidden_element_not_rendered_and_order_align():
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
            "text": "HIDDEN_MARK",
        },
        {
            "id": "c",
            "type": "static_text",
            "source": SOURCE_STATIC_TEXT,
            "label": "",
            "align": "right",
            "bold": False,
            "visible": True,
            "text": "VISIBLE_RIGHT",
        },
    ]
    design = resolve_print_design({"invoice": {"header_elements": elements}, "company_name": "ETERNAL PACKAGING"})
    text = _pdf_text(generate_sales_invoice_pdf_bytes(_invoice(), design=design))
    assert "HIDDEN_MARK" not in text
    assert "VISIBLE_RIGHT" in text
    assert "ETERNAL PACKAGING" in text


@pytest.mark.anyio
async def test_save_persists_header_elements():
    db = MockDb()
    elements = default_invoice_header_elements()[:3]
    payload = {
        "company_name": "ETERNAL PACKAGING",
        "address_lines": ["Alamat 1"],
        "accent_color": "#cc1f1a",
        "invoice": {
            "header_elements": elements,
        },
    }
    saved = await save_print_design_settings(db, payload, user={"email": "a@b.c", "name": "A"})
    assert saved["schema_version"] == 2
    assert len(saved["invoice"]["header_elements"]) == 3
    again = resolve_print_design(db.app_settings._docs[0])
    assert again["schema_version"] == 2
