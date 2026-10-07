"""Whitelisted print field registry for Design Cetakan Phase 1 (invoice/proforma header).

No user-controlled dotted DB paths. Keys are closed constants only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

DOCUMENT_INVOICE = "invoice"
DOCUMENT_PROFORMA = "proforma"
ALLOWED_DOCUMENTS = frozenset({DOCUMENT_INVOICE, DOCUMENT_PROFORMA})

ELEMENT_TYPE_FIELD = "field"
ELEMENT_TYPE_STATIC = "static_text"
ALLOWED_ELEMENT_TYPES = frozenset({ELEMENT_TYPE_FIELD, ELEMENT_TYPE_STATIC})

ALIGN_LEFT = "left"
ALIGN_CENTER = "center"
ALIGN_RIGHT = "right"
ALLOWED_ALIGNS = frozenset({ALIGN_LEFT, ALIGN_CENTER, ALIGN_RIGHT})

SOURCE_COMPANY_LOGO = "company.logo"
SOURCE_COMPANY_NAME = "company.name"
SOURCE_COMPANY_ADDRESS = "company.address"
SOURCE_CUSTOMER_NAME = "customer.name"
SOURCE_CUSTOMER_ADDRESS = "customer.address"
SOURCE_CUSTOMER_OUTLET = "customer.outlet"
SOURCE_DOCUMENT_NUMBER = "document.number"
SOURCE_DOCUMENT_DATE = "document.date"
SOURCE_DOCUMENT_DUE_DATE = "document.due_date"
SOURCE_DOCUMENT_PO_NUMBER = "document.po_number"
SOURCE_SALES_NAME = "sales.name"
SOURCE_STATIC_TEXT = "static.text"

# field sources only — static_text uses SOURCE_STATIC_TEXT with element.text
FIELD_SOURCES = frozenset({
    SOURCE_COMPANY_LOGO,
    SOURCE_COMPANY_NAME,
    SOURCE_COMPANY_ADDRESS,
    SOURCE_CUSTOMER_NAME,
    SOURCE_CUSTOMER_ADDRESS,
    SOURCE_CUSTOMER_OUTLET,
    SOURCE_DOCUMENT_NUMBER,
    SOURCE_DOCUMENT_DATE,
    SOURCE_DOCUMENT_DUE_DATE,
    SOURCE_DOCUMENT_PO_NUMBER,
    SOURCE_SALES_NAME,
})

VALUE_TYPE_TEXT = "text"
VALUE_TYPE_MULTILINE = "multiline"
VALUE_TYPE_IMAGE = "image"

MAX_HEADER_ELEMENTS = 24
MAX_ELEMENT_LABEL = 80
MAX_STATIC_TEXT = 240
MAX_ELEMENT_ID = 64


def _spec(
    key: str,
    *,
    label: str,
    group: str,
    group_label: str,
    value_type: str = VALUE_TYPE_TEXT,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "group": group,
        "group_label": group_label,
        "value_type": value_type,
        "documents": sorted(ALLOWED_DOCUMENTS),
    }


FIELD_REGISTRY: Tuple[Dict[str, Any], ...] = (
    _spec(SOURCE_COMPANY_LOGO, label="Logo", group="company", group_label="Perusahaan", value_type=VALUE_TYPE_IMAGE),
    _spec(SOURCE_COMPANY_NAME, label="Nama", group="company", group_label="Perusahaan"),
    _spec(SOURCE_COMPANY_ADDRESS, label="Alamat", group="company", group_label="Perusahaan", value_type=VALUE_TYPE_MULTILINE),
    _spec(SOURCE_CUSTOMER_NAME, label="Nama", group="customer", group_label="Customer"),
    _spec(SOURCE_CUSTOMER_ADDRESS, label="Alamat", group="customer", group_label="Customer", value_type=VALUE_TYPE_MULTILINE),
    _spec(SOURCE_CUSTOMER_OUTLET, label="Outlet", group="customer", group_label="Customer"),
    _spec(SOURCE_DOCUMENT_NUMBER, label="Nomor Faktur", group="document", group_label="Dokumen"),
    _spec(SOURCE_DOCUMENT_DATE, label="Tanggal", group="document", group_label="Dokumen"),
    _spec(SOURCE_DOCUMENT_DUE_DATE, label="Jatuh Tempo", group="document", group_label="Dokumen"),
    _spec(SOURCE_DOCUMENT_PO_NUMBER, label="No. PO", group="document", group_label="Dokumen"),
    _spec(SOURCE_SALES_NAME, label="Nama Sales", group="sales", group_label="Sales"),
    _spec(SOURCE_STATIC_TEXT, label="Teks Manual", group="static", group_label="Lainnya"),
)

_REGISTRY_BY_KEY = {row["key"]: row for row in FIELD_REGISTRY}


def field_spec(key: str) -> Optional[Dict[str, Any]]:
    return _REGISTRY_BY_KEY.get(str(key or "").strip())


def is_known_source(key: str) -> bool:
    return str(key or "").strip() in _REGISTRY_BY_KEY


def registry_catalog() -> List[Dict[str, Any]]:
    """UI-safe catalog (no resolvers)."""
    return [dict(row) for row in FIELD_REGISTRY]


def default_invoice_header_elements() -> List[Dict[str, Any]]:
    """Safe default list mirroring letterhead + common party fields (Phase 1 registry only).

    Does not include banks / expedition / shipping type — those remain V1-only.
    """
    return [
        {
            "id": "hdr-company-logo",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_COMPANY_LOGO,
            "label": "",
            "align": ALIGN_LEFT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-company-name",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_COMPANY_NAME,
            "label": "",
            "align": ALIGN_LEFT,
            "bold": True,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-company-address",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_COMPANY_ADDRESS,
            "label": "",
            "align": ALIGN_LEFT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-customer-name",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_CUSTOMER_NAME,
            "label": "Kepada",
            "align": ALIGN_LEFT,
            "bold": True,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-customer-address",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_CUSTOMER_ADDRESS,
            "label": "",
            "align": ALIGN_LEFT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-document-date",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_DOCUMENT_DATE,
            "label": "Tanggal",
            "align": ALIGN_RIGHT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-document-number",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_DOCUMENT_NUMBER,
            "label": "Nomor",
            "align": ALIGN_RIGHT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-sales-name",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_SALES_NAME,
            "label": "Sales",
            "align": ALIGN_RIGHT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-document-po",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_DOCUMENT_PO_NUMBER,
            "label": "PO No",
            "align": ALIGN_RIGHT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "hdr-document-due",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_DOCUMENT_DUE_DATE,
            "label": "Jatuh Tempo",
            "align": ALIGN_RIGHT,
            "bold": False,
            "visible": True,
            "text": "",
        },
    ]
