"""Build one-shot print contexts for Design Cetakan header/footer.

Uses design settings + document already in memory. No DB I/O.
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
    SOURCE_DOCUMENT_DELIVERY_DATE,
    SOURCE_DOCUMENT_DUE_DATE,
    SOURCE_DOCUMENT_EXPEDITION,
    SOURCE_DOCUMENT_NOTES,
    SOURCE_DOCUMENT_NUMBER,
    SOURCE_DOCUMENT_PAYMENT_TERMS,
    SOURCE_DOCUMENT_PO_NUMBER,
    SOURCE_DOCUMENT_ZONE,
    SOURCE_ESTIMASI_BLOCK,
    SOURCE_ESTIMASI_TITLE,
    SOURCE_PO_SIGNATURES_BLOCK,
    SOURCE_SALES_NAME,
    SOURCE_SIGNATURE_APPROVED,
    SOURCE_SIGNATURE_APPROVED_FOOTER,
    SOURCE_SIGNATURE_APPROVED_LABEL,
    SOURCE_SIGNATURE_PREPARED,
    SOURCE_SIGNATURE_PREPARED_FOOTER,
    SOURCE_SIGNATURE_PREPARED_LABEL,
    SOURCE_STATIC_TEXT,
    SOURCE_USER_CREATED_BY,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _address_lines(design: Optional[dict]) -> List[str]:
    lines = (design or {}).get("address_lines") if isinstance(design, dict) else None
    if not isinstance(lines, (list, tuple)):
        return []
    return [_text(line) for line in lines if _text(line)]


def _company_values(des: dict, assets: dict) -> Dict[str, Any]:
    logo_bytes = assets.get("company_logo") if isinstance(assets, dict) else None
    company_address = _address_lines(des)
    return {
        SOURCE_COMPANY_LOGO: logo_bytes if logo_bytes else None,
        SOURCE_COMPANY_NAME: _text(des.get("company_name")),
        SOURCE_COMPANY_ADDRESS: "\n".join(company_address),
        "_company_address_lines": company_address,
    }


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
    company = _company_values(des, assets or {})
    prepared_sig = assets.get("prepared_signature_image") if isinstance(assets, dict) else None
    approved_sig = assets.get("approved_signature_image") if isinstance(assets, dict) else None

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
        **{k: v for k, v in company.items() if not str(k).startswith("_")},
        SOURCE_CUSTOMER_NAME: _text(inv.get("customer")),
        SOURCE_CUSTOMER_ADDRESS: _text(inv.get("address")),
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
        SOURCE_ESTIMASI_BLOCK: {"title": estimasi_title, "lines": estimasi_lines},
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
            "address": company["_company_address_lines"],
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
        "sales": {"name": values[SOURCE_SALES_NAME]},
        "signature": {
            "prepared": values[SOURCE_SIGNATURE_PREPARED],
            "approved": values[SOURCE_SIGNATURE_APPROVED],
        },
        "estimasi": {"title": estimasi_title, "lines": estimasi_lines},
    }


def _po_delivery_date(order: dict) -> str:
    for key in (
        "tanggal_pengiriman",
        "effective_delivery_date",
        "delivery_deadline_effective",
        "scheduled_delivery_date",
        "estimated_delivery",
        "delivery_deadline_manual",
    ):
        raw = order.get(key)
        if _text(raw):
            return format_pdf_date(raw, fallback=_text(raw))
    return ""


def _po_notes(order: dict) -> str:
    parts: List[str] = []
    header = _text(order.get("notes"))
    if header:
        parts.append(header)
    for line in order.get("line_items") or []:
        if not isinstance(line, dict):
            continue
        note = _text(line.get("notes"))
        if note and note not in parts:
            parts.append(note)
    return "\n".join(parts)


def build_po_print_context(order: Optional[dict], design: Optional[dict]) -> Dict[str, Any]:
    ord_ = order if isinstance(order, dict) else {}
    des = design if isinstance(design, dict) else {}
    po_design = des.get("po") if isinstance(des.get("po"), dict) else {}
    assets = des.get("_pdf_assets") if isinstance(des.get("_pdf_assets"), dict) else {}
    company = _company_values(des, assets or {})
    labels = list(po_design.get("signature_labels") or ["Operator", "Pengiriman", "Penerima"])
    footer_name = _text(po_design.get("footer_name_label")) or "Nama"

    values: Dict[str, Any] = {
        **{k: v for k, v in company.items() if not str(k).startswith("_")},
        SOURCE_CUSTOMER_NAME: _text(ord_.get("customer")),
        SOURCE_CUSTOMER_ADDRESS: _text(ord_.get("address")),
        SOURCE_CUSTOMER_OUTLET: _text(ord_.get("outlet")),
        SOURCE_DOCUMENT_NUMBER: _text(ord_.get("po_number")),
        SOURCE_DOCUMENT_DATE: format_pdf_date(ord_.get("order_date") or ord_.get("created_at")),
        SOURCE_DOCUMENT_DELIVERY_DATE: _po_delivery_date(ord_),
        SOURCE_DOCUMENT_PAYMENT_TERMS: _text(ord_.get("payment_terms")),
        SOURCE_DOCUMENT_EXPEDITION: _text(ord_.get("expedition")),
        SOURCE_DOCUMENT_ZONE: _text(ord_.get("delivery_zone")),
        SOURCE_DOCUMENT_NOTES: _po_notes(ord_),
        SOURCE_SALES_NAME: _text(ord_.get("sales")),
        SOURCE_USER_CREATED_BY: _text(ord_.get("created_by_user")),
        SOURCE_STATIC_TEXT: "",
        SOURCE_PO_SIGNATURES_BLOCK: {
            "labels": [str(x) for x in labels],
            "footer_name": footer_name,
        },
    }
    return {
        "document_type": "po",
        "values": values,
        "company": {
            "name": values[SOURCE_COMPANY_NAME],
            "address": company["_company_address_lines"],
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
            "delivery_date": values[SOURCE_DOCUMENT_DELIVERY_DATE],
            "payment_terms": values[SOURCE_DOCUMENT_PAYMENT_TERMS],
            "expedition": values[SOURCE_DOCUMENT_EXPEDITION],
            "zone": values[SOURCE_DOCUMENT_ZONE],
            "notes": values[SOURCE_DOCUMENT_NOTES],
        },
        "sales": {"name": values[SOURCE_SALES_NAME]},
        "user": {"created_by": values[SOURCE_USER_CREATED_BY]},
        "po_signatures": values[SOURCE_PO_SIGNATURES_BLOCK],
    }


def build_delivery_print_context(delivery: Optional[dict], design: Optional[dict]) -> Dict[str, Any]:
    deliv = delivery if isinstance(delivery, dict) else {}
    des = design if isinstance(design, dict) else {}
    assets = des.get("_pdf_assets") if isinstance(des.get("_pdf_assets"), dict) else {}
    company = _company_values(des, assets or {})
    note_parts: List[str] = []
    header_note = _text(deliv.get("notes"))
    if header_note:
        note_parts.append(header_note)
    for line in deliv.get("line_items") or []:
        if not isinstance(line, dict):
            continue
        note = _text(line.get("notes"))
        if note and note not in note_parts and note not in ("-",):
            note_parts.append(note)

    values: Dict[str, Any] = {
        **{k: v for k, v in company.items() if not str(k).startswith("_")},
        SOURCE_CUSTOMER_NAME: _text(deliv.get("customer")),
        SOURCE_CUSTOMER_ADDRESS: _text(deliv.get("address")),
        SOURCE_CUSTOMER_OUTLET: _text(deliv.get("outlet")),
        SOURCE_DOCUMENT_NUMBER: _text(deliv.get("sj_number")),
        SOURCE_DOCUMENT_DATE: format_pdf_date(deliv.get("delivery_date")),
        SOURCE_DOCUMENT_DELIVERY_DATE: format_pdf_date(deliv.get("delivery_date")),
        SOURCE_DOCUMENT_PO_NUMBER: _text(deliv.get("po_number")),
        SOURCE_DOCUMENT_EXPEDITION: _text(deliv.get("expedition")),
        SOURCE_DOCUMENT_ZONE: _text(deliv.get("delivery_zone")),
        SOURCE_DOCUMENT_NOTES: "\n".join(note_parts),
        SOURCE_SALES_NAME: _text(deliv.get("sales")),
        SOURCE_USER_CREATED_BY: _text(deliv.get("created_by_name")),
        SOURCE_STATIC_TEXT: "",
    }
    return {
        "document_type": "delivery",
        "values": values,
        "company": {
            "name": values[SOURCE_COMPANY_NAME],
            "address": company["_company_address_lines"],
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
            "delivery_date": values[SOURCE_DOCUMENT_DELIVERY_DATE],
            "po_number": values[SOURCE_DOCUMENT_PO_NUMBER],
            "expedition": values[SOURCE_DOCUMENT_EXPEDITION],
            "zone": values[SOURCE_DOCUMENT_ZONE],
            "notes": values[SOURCE_DOCUMENT_NOTES],
        },
        "sales": {"name": values[SOURCE_SALES_NAME]},
        "user": {"created_by": values[SOURCE_USER_CREATED_BY]},
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
