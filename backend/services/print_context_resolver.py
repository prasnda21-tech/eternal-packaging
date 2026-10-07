"""Build a one-shot print context for invoice/proforma Design Cetakan header/footer.

Uses design settings + invoice document already in memory. No DB I/O.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.pdf_date import format_pdf_date
from services.print_field_registry import (
    SOURCE_COMPANY_ADDRESS,
    SOURCE_COMPANY_LOGO,
    SOURCE_COMPANY_NAME,
    SOURCE_CUSTOMER_ADDRESS,
    SOURCE_CUSTOMER_NAME,
    SOURCE_CUSTOMER_OUTLET,
    SOURCE_DOCUMENT_DATE,
    SOURCE_DOCUMENT_DUE_DATE,
    SOURCE_DOCUMENT_NUMBER,
    SOURCE_DOCUMENT_PO_NUMBER,
    SOURCE_ESTIMASI_BLOCK,
    SOURCE_ESTIMASI_TITLE,
    SOURCE_SALES_NAME,
    SOURCE_SIGNATURE_APPROVED,
    SOURCE_SIGNATURE_APPROVED_FOOTER,
    SOURCE_SIGNATURE_APPROVED_LABEL,
    SOURCE_SIGNATURE_PREPARED,
    SOURCE_SIGNATURE_PREPARED_FOOTER,
    SOURCE_SIGNATURE_PREPARED_LABEL,
    SOURCE_STATIC_TEXT,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _address_lines(design: Optional[dict]) -> List[str]:
    lines = (design or {}).get("address_lines") if isinstance(design, dict) else None
    if not isinstance(lines, (list, tuple)):
        return []
    return [_text(line) for line in lines if _text(line)]


def build_invoice_print_context(
    invoice: Optional[dict],
    design: Optional[dict],
    *,
    document_type: str = "invoice",
) -> Dict[str, Any]:
    """Resolved values keyed by registry source. Pure function."""
    from services.sales_invoices import invoice_sales_label

    inv = invoice if isinstance(invoice, dict) else {}
    des = design if isinstance(design, dict) else {}
    inv_design = des.get("invoice") if isinstance(des.get("invoice"), dict) else {}
    assets = des.get("_pdf_assets") if isinstance(des.get("_pdf_assets"), dict) else {}
    logo_bytes = assets.get("company_logo") if isinstance(assets, dict) else None
    prepared_sig = assets.get("prepared_signature_image") if isinstance(assets, dict) else None
    approved_sig = assets.get("approved_signature_image") if isinstance(assets, dict) else None

    company_address = _address_lines(des)
    customer_address = _text(inv.get("address"))
    due_raw = inv.get("due_date")
    due_shown = format_pdf_date(due_raw) if due_raw not in (None, "") else ""
    sales_name = _text(invoice_sales_label(inv))
    estimasi_title = _text(inv_design.get("estimasi_title"))
    estimasi_lines = [
        _text(line)
        for line in (inv_design.get("estimasi_lines") or [])
        if _text(line)
    ]
    prepared_label = _text(inv_design.get("prepared_label")) or "Disiapkan Oleh"
    prepared_footer = _text(inv_design.get("prepared_footer")) or "Admin"
    approved_label = _text(inv_design.get("approved_label")) or "Disetujui Oleh"
    approved_footer = _text(inv_design.get("approved_footer")) or "Pengelola"

    values: Dict[str, Any] = {
        SOURCE_COMPANY_LOGO: logo_bytes if logo_bytes else None,
        SOURCE_COMPANY_NAME: _text(des.get("company_name")),
        SOURCE_COMPANY_ADDRESS: "\n".join(company_address),
        SOURCE_CUSTOMER_NAME: _text(inv.get("customer")),
        SOURCE_CUSTOMER_ADDRESS: customer_address,
        SOURCE_CUSTOMER_OUTLET: _text(inv.get("outlet")),
        SOURCE_DOCUMENT_NUMBER: _text(inv.get("invoice_number") or inv.get("id")),
        SOURCE_DOCUMENT_DATE: format_pdf_date(inv.get("invoice_date")),
        SOURCE_DOCUMENT_DUE_DATE: due_shown,
        SOURCE_DOCUMENT_PO_NUMBER: _text(inv.get("po_number")),
        SOURCE_SALES_NAME: sales_name,
        SOURCE_STATIC_TEXT: "",
        SOURCE_SIGNATURE_PREPARED_LABEL: prepared_label,
        SOURCE_SIGNATURE_PREPARED_FOOTER: prepared_footer,
        SOURCE_SIGNATURE_APPROVED_LABEL: approved_label,
        SOURCE_SIGNATURE_APPROVED_FOOTER: approved_footer,
        SOURCE_ESTIMASI_TITLE: estimasi_title,
        SOURCE_ESTIMASI_BLOCK: {
            "title": estimasi_title,
            "lines": estimasi_lines,
        },
        SOURCE_SIGNATURE_PREPARED: {
            "label": prepared_label,
            "footer": prepared_footer,
            "image": prepared_sig if prepared_sig else None,
            "middle": "",
        },
        SOURCE_SIGNATURE_APPROVED: {
            "label": approved_label,
            "footer": approved_footer,
            "image": approved_sig if approved_sig else None,
            "middle": sales_name,
        },
    }
    return {
        "document_type": str(document_type or "invoice"),
        "values": values,
        "company": {
            "name": values[SOURCE_COMPANY_NAME],
            "address": company_address,
            "logo": values[SOURCE_COMPANY_LOGO],
        },
        "customer": {
            "name": values[SOURCE_CUSTOMER_NAME],
            "address": values[SOURCE_CUSTOMER_ADDRESS],
            "outlet": values[SOURCE_CUSTOMER_OUTLET],
        },
        "document": {
            "number": values[SOURCE_DOCUMENT_NUMBER],
            "date": values[SOURCE_DOCUMENT_DATE],
            "due_date": values[SOURCE_DOCUMENT_DUE_DATE],
            "po_number": values[SOURCE_DOCUMENT_PO_NUMBER],
        },
        "sales": {
            "name": values[SOURCE_SALES_NAME],
        },
        "signature": {
            "prepared": values[SOURCE_SIGNATURE_PREPARED],
            "approved": values[SOURCE_SIGNATURE_APPROVED],
        },
        "estimasi": {
            "title": estimasi_title,
            "lines": estimasi_lines,
        },
    }


def resolve_source_value(context: Optional[dict], source: str, *, static_text: str = "") -> Any:
    """Lookup one registry key from context. static.text uses element text."""
    key = str(source or "").strip()
    if key == SOURCE_STATIC_TEXT:
        return _text(static_text)
    values = (context or {}).get("values") if isinstance(context, dict) else None
    if not isinstance(values, dict):
        return ""
    return values.get(key)
