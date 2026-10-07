"""Whitelisted print field registry for Design Cetakan Phase 1–3.

No user-controlled dotted DB paths. Keys are closed constants only.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

DOCUMENT_INVOICE = "invoice"
DOCUMENT_PROFORMA = "proforma"
DOCUMENT_PO = "po"
DOCUMENT_DELIVERY = "delivery"
ALLOWED_DOCUMENTS = frozenset({
    DOCUMENT_INVOICE,
    DOCUMENT_PROFORMA,
    DOCUMENT_PO,
    DOCUMENT_DELIVERY,
})
INVOICE_FAMILY = frozenset({DOCUMENT_INVOICE, DOCUMENT_PROFORMA})

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
SOURCE_DOCUMENT_DELIVERY_DATE = "document.delivery_date"
SOURCE_DOCUMENT_PAYMENT_TERMS = "document.payment_terms"
SOURCE_DOCUMENT_EXPEDITION = "document.expedition"
SOURCE_DOCUMENT_ZONE = "document.zone"
SOURCE_DOCUMENT_NOTES = "document.notes"
SOURCE_SALES_NAME = "sales.name"
SOURCE_USER_CREATED_BY = "user.created_by"
SOURCE_STATIC_TEXT = "static.text"

SOURCE_SIGNATURE_PREPARED = "signature.prepared"
SOURCE_SIGNATURE_APPROVED = "signature.approved"
SOURCE_SIGNATURE_PREPARED_LABEL = "signature.prepared_label"
SOURCE_SIGNATURE_PREPARED_FOOTER = "signature.prepared_footer"
SOURCE_SIGNATURE_APPROVED_LABEL = "signature.approved_label"
SOURCE_SIGNATURE_APPROVED_FOOTER = "signature.approved_footer"
SOURCE_ESTIMASI_TITLE = "estimasi.title"
SOURCE_ESTIMASI_BLOCK = "estimasi.block"
SOURCE_PO_SIGNATURES_BLOCK = "po.signatures_block"

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
    SOURCE_DOCUMENT_DELIVERY_DATE,
    SOURCE_DOCUMENT_PAYMENT_TERMS,
    SOURCE_DOCUMENT_EXPEDITION,
    SOURCE_DOCUMENT_ZONE,
    SOURCE_DOCUMENT_NOTES,
    SOURCE_SALES_NAME,
    SOURCE_USER_CREATED_BY,
    SOURCE_PO_SIGNATURES_BLOCK,
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
    SOURCE_PO_SIGNATURES_BLOCK,
})

FOOTER_FIELD_SOURCES = FIELD_SOURCES | FOOTER_TEXT_SOURCES

VALUE_TYPE_TEXT = "text"
VALUE_TYPE_MULTILINE = "multiline"
VALUE_TYPE_IMAGE = "image"
VALUE_TYPE_SIGNATURE = "signature"
VALUE_TYPE_ESTIMASI = "estimasi_block"
VALUE_TYPE_PO_SIGNATURES = "po_signatures_block"

MAX_HEADER_ELEMENTS = 24
MAX_FOOTER_ELEMENTS = 24
MAX_ELEMENT_LABEL = 80
MAX_STATIC_TEXT = 240
MAX_ELEMENT_ID = 64

SECTION_HEADER = "header"
SECTION_FOOTER = "footer"

_ALL_DOCS = (DOCUMENT_INVOICE, DOCUMENT_PROFORMA, DOCUMENT_PO, DOCUMENT_DELIVERY)
_INV = (DOCUMENT_INVOICE, DOCUMENT_PROFORMA)
_PO = (DOCUMENT_PO,)
_SJ = (DOCUMENT_DELIVERY,)
_PO_SJ = (DOCUMENT_PO, DOCUMENT_DELIVERY)
_ALL_BUT_INV_SIG = _ALL_DOCS


def _spec(
    key: str,
    *,
    label: str,
    group: str,
    group_label: str,
    value_type: str = VALUE_TYPE_TEXT,
    sections: Optional[Tuple[str, ...]] = None,
    documents: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    docs = tuple(documents) if documents is not None else _ALL_DOCS
    return {
        "key": key,
        "label": label,
        "group": group,
        "group_label": group_label,
        "value_type": value_type,
        "documents": sorted(docs),
        "sections": list(sections or (SECTION_HEADER, SECTION_FOOTER)),
    }


FIELD_REGISTRY: Tuple[Dict[str, Any], ...] = (
    _spec(SOURCE_COMPANY_LOGO, label="Logo", group="company", group_label="Perusahaan", value_type=VALUE_TYPE_IMAGE),
    _spec(SOURCE_COMPANY_NAME, label="Nama", group="company", group_label="Perusahaan"),
    _spec(SOURCE_COMPANY_ADDRESS, label="Alamat", group="company", group_label="Perusahaan", value_type=VALUE_TYPE_MULTILINE),
    _spec(SOURCE_CUSTOMER_NAME, label="Nama", group="customer", group_label="Customer"),
    _spec(SOURCE_CUSTOMER_ADDRESS, label="Alamat", group="customer", group_label="Customer", value_type=VALUE_TYPE_MULTILINE),
    _spec(SOURCE_CUSTOMER_OUTLET, label="Outlet", group="customer", group_label="Customer", documents=_SJ + _INV),
    _spec(SOURCE_DOCUMENT_NUMBER, label="Nomor Dokumen", group="document", group_label="Dokumen"),
    _spec(SOURCE_DOCUMENT_DATE, label="Tanggal", group="document", group_label="Dokumen"),
    _spec(SOURCE_DOCUMENT_DUE_DATE, label="Jatuh Tempo", group="document", group_label="Dokumen", documents=_INV),
    _spec(SOURCE_DOCUMENT_PO_NUMBER, label="No. PO", group="document", group_label="Dokumen", documents=_INV + _SJ),
    _spec(SOURCE_DOCUMENT_DELIVERY_DATE, label="Tanggal Pengiriman", group="document", group_label="Dokumen", documents=_PO_SJ),
    _spec(SOURCE_DOCUMENT_PAYMENT_TERMS, label="Syarat Pembayaran", group="document", group_label="Dokumen", documents=_PO),
    _spec(SOURCE_DOCUMENT_EXPEDITION, label="Ekspedisi", group="document", group_label="Dokumen", documents=_PO_SJ),
    _spec(SOURCE_DOCUMENT_ZONE, label="Zona Pengiriman", group="document", group_label="Dokumen", documents=_PO_SJ),
    _spec(SOURCE_DOCUMENT_NOTES, label="Keterangan", group="document", group_label="Dokumen", value_type=VALUE_TYPE_MULTILINE, documents=_PO_SJ),
    _spec(SOURCE_SALES_NAME, label="Nama Sales", group="sales", group_label="Sales"),
    _spec(SOURCE_USER_CREATED_BY, label="Pembuat Data", group="user", group_label="User", documents=_PO_SJ),
    _spec(
        SOURCE_SIGNATURE_PREPARED,
        label="Tanda Tangan Disiapkan",
        group="signature",
        group_label="Tanda Tangan",
        value_type=VALUE_TYPE_SIGNATURE,
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_SIGNATURE_APPROVED,
        label="Tanda Tangan Disetujui",
        group="signature",
        group_label="Tanda Tangan",
        value_type=VALUE_TYPE_SIGNATURE,
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_SIGNATURE_PREPARED_LABEL,
        label="Label Disiapkan",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_SIGNATURE_PREPARED_FOOTER,
        label="Footer Disiapkan",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_SIGNATURE_APPROVED_LABEL,
        label="Label Disetujui",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_SIGNATURE_APPROVED_FOOTER,
        label="Footer Disetujui",
        group="signature",
        group_label="Tanda Tangan",
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_ESTIMASI_TITLE,
        label="Judul Estimasi",
        group="estimasi",
        group_label="Estimasi",
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_ESTIMASI_BLOCK,
        label="Blok Estimasi",
        group="estimasi",
        group_label="Estimasi",
        value_type=VALUE_TYPE_ESTIMASI,
        sections=(SECTION_FOOTER,),
        documents=_INV,
    ),
    _spec(
        SOURCE_PO_SIGNATURES_BLOCK,
        label="Blok Tanda Tangan PO",
        group="signature",
        group_label="Tanda Tangan",
        value_type=VALUE_TYPE_PO_SIGNATURES,
        sections=(SECTION_FOOTER,),
        documents=_PO,
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


def source_allowed_for_document(key: str, document: str) -> bool:
    spec = field_spec(key)
    if not spec:
        return False
    return str(document or "").strip() in (spec.get("documents") or [])


def registry_catalog(*, section: Optional[str] = None, document: Optional[str] = None) -> List[Dict[str, Any]]:
    """UI-safe catalog (no resolvers)."""
    rows = [dict(row) for row in FIELD_REGISTRY]
    if section:
        rows = [row for row in rows if section in (row.get("sections") or [])]
    if document:
        rows = [row for row in rows if document in (row.get("documents") or [])]
    return rows


def _el(eid, source, *, typ="field", label="", align="left", bold=False, visible=True, text=""):
    return {
        "id": eid,
        "type": typ,
        "source": source,
        "label": label,
        "align": align,
        "bold": bold,
        "visible": visible,
        "text": text,
    }


def default_invoice_header_elements() -> List[Dict[str, Any]]:
    return [
        _el("hdr-company-logo", SOURCE_COMPANY_LOGO, align=ALIGN_LEFT),
        _el("hdr-company-name", SOURCE_COMPANY_NAME, align=ALIGN_LEFT, bold=True),
        _el("hdr-company-address", SOURCE_COMPANY_ADDRESS, align=ALIGN_LEFT),
        _el("hdr-customer-name", SOURCE_CUSTOMER_NAME, label="Kepada", align=ALIGN_LEFT, bold=True),
        _el("hdr-customer-address", SOURCE_CUSTOMER_ADDRESS, align=ALIGN_LEFT),
        _el("hdr-document-date", SOURCE_DOCUMENT_DATE, label="Tanggal", align=ALIGN_RIGHT),
        _el("hdr-document-number", SOURCE_DOCUMENT_NUMBER, label="Nomor", align=ALIGN_RIGHT),
        _el("hdr-sales-name", SOURCE_SALES_NAME, label="Sales", align=ALIGN_RIGHT),
        _el("hdr-document-po", SOURCE_DOCUMENT_PO_NUMBER, label="PO No", align=ALIGN_RIGHT),
        _el("hdr-document-due", SOURCE_DOCUMENT_DUE_DATE, label="Jatuh Tempo", align=ALIGN_RIGHT),
    ]


def default_invoice_footer_elements() -> List[Dict[str, Any]]:
    return [
        _el("ftr-sig-prepared", SOURCE_SIGNATURE_PREPARED, typ=ELEMENT_TYPE_SIGNATURE, align=ALIGN_LEFT),
        _el("ftr-sig-approved", SOURCE_SIGNATURE_APPROVED, typ=ELEMENT_TYPE_SIGNATURE, align=ALIGN_CENTER),
        _el("ftr-estimasi-block", SOURCE_ESTIMASI_BLOCK, align=ALIGN_RIGHT),
    ]


def default_po_header_elements() -> List[Dict[str, Any]]:
    return [
        _el("po-hdr-logo", SOURCE_COMPANY_LOGO, align=ALIGN_LEFT),
        _el("po-hdr-company", SOURCE_COMPANY_NAME, align=ALIGN_LEFT, bold=True),
        _el("po-hdr-address", SOURCE_COMPANY_ADDRESS, align=ALIGN_LEFT),
        _el("po-hdr-customer", SOURCE_CUSTOMER_NAME, label="Kepada", align=ALIGN_LEFT, bold=True),
        _el("po-hdr-cust-addr", SOURCE_CUSTOMER_ADDRESS, align=ALIGN_LEFT),
        _el("po-hdr-date", SOURCE_DOCUMENT_DATE, label="Tanggal", align=ALIGN_RIGHT),
        _el("po-hdr-number", SOURCE_DOCUMENT_NUMBER, label="Nomor", align=ALIGN_RIGHT),
        _el("po-hdr-terms", SOURCE_DOCUMENT_PAYMENT_TERMS, label="Syarat Pembayaran", align=ALIGN_RIGHT),
        _el("po-hdr-user", SOURCE_USER_CREATED_BY, label="User", align=ALIGN_RIGHT),
        _el("po-hdr-exp", SOURCE_DOCUMENT_EXPEDITION, label="Ekspedisi", align=ALIGN_RIGHT),
        _el("po-hdr-deliv", SOURCE_DOCUMENT_DELIVERY_DATE, label="Estimasi Tanggal Pengiriman", align=ALIGN_RIGHT),
        _el("po-hdr-zone", SOURCE_DOCUMENT_ZONE, label="Zona Pengiriman", align=ALIGN_RIGHT),
        _el("po-hdr-sales", SOURCE_SALES_NAME, label="Penjual", align=ALIGN_RIGHT),
    ]


def default_po_footer_elements() -> List[Dict[str, Any]]:
    return [
        _el("po-ftr-signatures", SOURCE_PO_SIGNATURES_BLOCK, align=ALIGN_CENTER),
    ]


def default_delivery_header_elements() -> List[Dict[str, Any]]:
    return [
        _el("sj-hdr-logo", SOURCE_COMPANY_LOGO, align=ALIGN_LEFT),
        _el("sj-hdr-company", SOURCE_COMPANY_NAME, align=ALIGN_LEFT, bold=True),
        _el("sj-hdr-address", SOURCE_COMPANY_ADDRESS, align=ALIGN_LEFT),
        _el("sj-hdr-customer", SOURCE_CUSTOMER_NAME, label="Kepada", align=ALIGN_LEFT, bold=True),
        _el("sj-hdr-cust-addr", SOURCE_CUSTOMER_ADDRESS, align=ALIGN_LEFT),
        _el("sj-hdr-sales", SOURCE_SALES_NAME, label="Nama Penjual", align=ALIGN_RIGHT),
        _el("sj-hdr-number", SOURCE_DOCUMENT_NUMBER, label="Nomor", align=ALIGN_RIGHT),
        _el("sj-hdr-exp", SOURCE_DOCUMENT_EXPEDITION, label="Ekspedisi", align=ALIGN_RIGHT),
        _el("sj-hdr-user", SOURCE_USER_CREATED_BY, label="Pembuat Data", align=ALIGN_RIGHT),
        _el("sj-hdr-deliv", SOURCE_DOCUMENT_DELIVERY_DATE, label="Tanggal Pengiriman", align=ALIGN_RIGHT),
        _el("sj-hdr-zone", SOURCE_DOCUMENT_ZONE, label="Zona Kirim", align=ALIGN_RIGHT),
        _el("sj-hdr-po", SOURCE_DOCUMENT_PO_NUMBER, label="Nomor PO", align=ALIGN_RIGHT),
    ]


def default_delivery_footer_elements() -> List[Dict[str, Any]]:
    """Presentation-only defaults. POD/driver evidence stays in frozen legacy block."""
    return [
        _el("sj-ftr-notes", SOURCE_DOCUMENT_NOTES, label="Keterangan", align=ALIGN_LEFT),
    ]
