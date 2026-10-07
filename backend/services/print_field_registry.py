"""Whitelisted print field registry for Design Cetakan Phase 1–2 (invoice/proforma).

No user-controlled dotted DB paths. Keys are closed constants only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

DOCUMENT_INVOICE = "invoice"
DOCUMENT_PROFORMA = "proforma"
ALLOWED_DOCUMENTS = frozenset({DOCUMENT_INVOICE, DOCUMENT_PROFORMA})

ELEMENT_TYPE_FIELD = "field"
ELEMENT_TYPE_STATIC = "static_text"
ELEMENT_TYPE_SIGNATURE = "signature"
ALLOWED_ELEMENT_TYPES = frozenset({ELEMENT_TYPE_FIELD, ELEMENT_TYPE_STATIC})
ALLOWED_FOOTER_ELEMENT_TYPES = frozenset({
    ELEMENT_TYPE_FIELD,
    ELEMENT_TYPE_STATIC,
    ELEMENT_TYPE_SIGNATURE,
})

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

SOURCE_SIGNATURE_PREPARED = "signature.prepared"
SOURCE_SIGNATURE_APPROVED = "signature.approved"
SOURCE_SIGNATURE_PREPARED_LABEL = "signature.prepared_label"
SOURCE_SIGNATURE_PREPARED_FOOTER = "signature.prepared_footer"
SOURCE_SIGNATURE_APPROVED_LABEL = "signature.approved_label"
SOURCE_SIGNATURE_APPROVED_FOOTER = "signature.approved_footer"
SOURCE_ESTIMASI_TITLE = "estimasi.title"
SOURCE_ESTIMASI_BLOCK = "estimasi.block"

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

SIGNATURE_SOURCES = frozenset({
    SOURCE_SIGNATURE_PREPARED,
    SOURCE_SIGNATURE_APPROVED,
})

FOOTER_TEXT_SOURCES = frozenset({
    SOURCE_SIGNATURE_PREPARED_LABEL,
    SOURCE_SIGNATURE_PREPARED_FOOTER,
    SOURCE_SIGNATURE_APPROVED_LABEL,
    SOURCE_SIGNATURE_APPROVED_FOOTER,
    SOURCE_ESTIMASI_TITLE,
    SOURCE_ESTIMASI_BLOCK,
})

FOOTER_FIELD_SOURCES = FIELD_SOURCES | FOOTER_TEXT_SOURCES

VALUE_TYPE_TEXT = "text"
VALUE_TYPE_MULTILINE = "multiline"
VALUE_TYPE_IMAGE = "image"
VALUE_TYPE_SIGNATURE = "signature"
VALUE_TYPE_ESTIMASI = "estimasi_block"

MAX_HEADER_ELEMENTS = 24
MAX_FOOTER_ELEMENTS = 24
MAX_ELEMENT_LABEL = 80
MAX_STATIC_TEXT = 240
MAX_ELEMENT_ID = 64

SECTION_HEADER = "header"
SECTION_FOOTER = "footer"


def _spec(
    key: str,
    *,
    label: str,
    group: str,
    group_label: str,
    value_type: str = VALUE_TYPE_TEXT,
    sections: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Any]:
    return {
        "key": key,
        "label": label,
        "group": group,
        "group_label": group_label,
        "value_type": value_type,
        "documents": sorted(ALLOWED_DOCUMENTS),
        "sections": list(sections or (SECTION_HEADER, SECTION_FOOTER)),
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
    _spec(
        SOURCE_SIGNATURE_PREPARED,
        label="Tanda Tangan Disiapkan",
        group="signature",
        group_label="Tanda Tangan",
        value_type=VALUE_TYPE_SIGNATURE,
        sections=(SECTION_FOOTER,),
    ),
    _spec(
        SOURCE_SIGNATURE_APPROVED,
        label="Tanda Tangan Disetujui",
        group="signature",
        group_label="Tanda Tangan",
        value_type=VALUE_TYPE_SIGNATURE,
        sections=(SECTION_FOOTER,),
    ),
    _spec(
        SOURCE_SIGNATURE_PREPARED_LABEL,
        label="Label Disiapkan",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
    ),
    _spec(
        SOURCE_SIGNATURE_PREPARED_FOOTER,
        label="Footer Disiapkan",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
    ),
    _spec(
        SOURCE_SIGNATURE_APPROVED_LABEL,
        label="Label Disetujui",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
    ),
    _spec(
        SOURCE_SIGNATURE_APPROVED_FOOTER,
        label="Footer Disetujui",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
    ),
    _spec(
        SOURCE_ESTIMASI_TITLE,
        label="Judul Estimasi",
        group="estimasi",
        group_label="Estimasi",
        sections=(SECTION_FOOTER,),
    ),
    _spec(
        SOURCE_ESTIMASI_BLOCK,
        label="Blok Estimasi",
        group="estimasi",
        group_label="Estimasi",
        value_type=VALUE_TYPE_ESTIMASI,
        sections=(SECTION_FOOTER,),
    ),
    _spec(SOURCE_STATIC_TEXT, label="Teks Manual", group="static", group_label="Lainnya"),
)

_REGISTRY_BY_KEY = {row["key"]: row for row in FIELD_REGISTRY}


def field_spec(key: str) -> Optional[Dict[str, Any]]:
    return _REGISTRY_BY_KEY.get(str(key or "").strip())


def is_known_source(key: str) -> bool:
    return str(key or "").strip() in _REGISTRY_BY_KEY


def is_footer_source(key: str) -> bool:
    spec = field_spec(key)
    if not spec:
        return False
    return SECTION_FOOTER in (spec.get("sections") or [])


def is_header_source(key: str) -> bool:
    spec = field_spec(key)
    if not spec:
        return False
    return SECTION_HEADER in (spec.get("sections") or [])


def registry_catalog(*, section: Optional[str] = None) -> List[Dict[str, Any]]:
    """UI-safe catalog (no resolvers)."""
    rows = [dict(row) for row in FIELD_REGISTRY]
    if section:
        rows = [row for row in rows if section in (row.get("sections") or [])]
    return rows


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


def default_invoice_footer_elements() -> List[Dict[str, Any]]:
    """Approximate legacy footer: prepared | approved | estimasi block."""
    return [
        {
            "id": "ftr-sig-prepared",
            "type": ELEMENT_TYPE_SIGNATURE,
            "source": SOURCE_SIGNATURE_PREPARED,
            "label": "",
            "align": ALIGN_LEFT,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "ftr-sig-approved",
            "type": ELEMENT_TYPE_SIGNATURE,
            "source": SOURCE_SIGNATURE_APPROVED,
            "label": "",
            "align": ALIGN_CENTER,
            "bold": False,
            "visible": True,
            "text": "",
        },
        {
            "id": "ftr-estimasi-block",
            "type": ELEMENT_TYPE_FIELD,
            "source": SOURCE_ESTIMASI_BLOCK,
            "label": "",
            "align": ALIGN_RIGHT,
            "bold": False,
            "visible": True,
            "text": "",
        },
    ]
