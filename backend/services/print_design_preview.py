"""Design Cetakan PDF preview using production renderers and dummy documents.

Saved configuration only. Never writes business records or storage objects.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import HTTPException

from services.order_pdf import generate_sales_order_pdf_bytes
from services.print_design_settings import assert_known_branch_id, get_print_design_for_pdf
from services.sales_deliveries import generate_sales_delivery_pdf_bytes
from services.sales_invoices import DOCUMENT_MARKUP, DOCUMENT_PROFORMA, generate_sales_invoice_pdf_bytes

PREVIEW_TYPES = frozenset({"po", "invoice", "proforma", "markup", "delivery"})

SAMPLE_CUSTOMER = "PT Contoh Cetakan"
SAMPLE_ADDRESS = "Jl. Contoh No. 10, Yogyakarta"
SAMPLE_SALES = "Sales Eternal"
SAMPLE_DATE = "2026-01-15"

PREVIEW_FILENAMES = {
    "po": "Preview_Pesanan_Penjualan.pdf",
    "invoice": "Preview_Faktur_Penjualan.pdf",
    "proforma": "Preview_Proforma.pdf",
    "markup": "Preview_Faktur_Markup.pdf",
    "delivery": "Preview_Surat_Jalan.pdf",
}


def _sample_lines():
    return [
        {
            "item_code": "CUP-12",
            "name": "Cup PET 12 oz",
            "item_name": "Cup PET 12 oz",
            "quantity": 1000,
            "qty": 1000,
            "unit": "pcs",
            "color": "Transparan",
            "unit_price": 500,
            "line_subtotal": 500000,
        },
        {
            "item_code": "LID-12",
            "name": "Lid 12 oz",
            "item_name": "Lid 12 oz",
            "quantity": 1000,
            "qty": 1000,
            "unit": "pcs",
            "color": "Transparan",
            "unit_price": 200,
            "line_subtotal": 200000,
        },
    ]


def dummy_order() -> Dict[str, Any]:
    lines = _sample_lines()
    return {
        "po_number": "PREVIEW/PO/0001",
        "order_date": SAMPLE_DATE,
        "customer": SAMPLE_CUSTOMER,
        "address": SAMPLE_ADDRESS,
        "payment_terms": "Tempo 14 hari",
        "expedition": "Internal",
        "tanggal_pengiriman": SAMPLE_DATE,
        "delivery_zone": "Dalam Kota",
        "sales": SAMPLE_SALES,
        "created_by_user": "Preview",
        "notes": "Dokumen contoh Design Cetakan. Bukan data produksi.",
        "line_items": [
            {
                "line_idx": 0,
                "item_code": lines[0]["item_code"],
                "name": lines[0]["name"],
                "quantity": lines[0]["quantity"],
                "unit": lines[0]["unit"],
                "color": lines[0]["color"],
            },
            {
                "line_idx": 1,
                "item_code": lines[1]["item_code"],
                "name": lines[1]["name"],
                "quantity": lines[1]["quantity"],
                "unit": lines[1]["unit"],
                "color": lines[1]["color"],
            },
        ],
        "total_quantity": 2000,
        "operator_splits": [],
    }


def dummy_invoice() -> Dict[str, Any]:
    lines = _sample_lines()
    return {
        "invoice_number": "PREVIEW/INV/0001",
        "invoice_date": SAMPLE_DATE,
        "due_date": "2026-01-29",
        "customer": SAMPLE_CUSTOMER,
        "address": SAMPLE_ADDRESS,
        "outlet": "Outlet Contoh",
        "sales": SAMPLE_SALES,
        "po_number": "PREVIEW/PO/0001",
        "expedition": "Internal",
        "shipping_type": "Pengiriman",
        "lines": [
            {
                "item_code": lines[0]["item_code"],
                "item_name": lines[0]["item_name"],
                "quantity": lines[0]["quantity"],
                "unit_price": lines[0]["unit_price"],
                "line_subtotal": lines[0]["line_subtotal"],
            },
            {
                "item_code": lines[1]["item_code"],
                "item_name": lines[1]["item_name"],
                "quantity": lines[1]["quantity"],
                "unit_price": lines[1]["unit_price"],
                "line_subtotal": lines[1]["line_subtotal"],
            },
        ],
        "other_charges": [],
        "subtotal": 700000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "paid_total": 0,
        "grand_total": 700000,
    }


def dummy_delivery() -> Dict[str, Any]:
    lines = _sample_lines()
    return {
        "sj_number": "PREVIEW/SJ/0001",
        "customer": SAMPLE_CUSTOMER,
        "address": SAMPLE_ADDRESS,
        "sales": SAMPLE_SALES,
        "expedition": "Internal",
        "delivery_date": SAMPLE_DATE,
        "delivery_zone": "Dalam Kota",
        "created_by_name": "Preview",
        "po_number": "PREVIEW/PO/0001",
        "lines": [
            {
                "item_code": lines[0]["item_code"],
                "item_name": lines[0]["item_name"],
                "qty": lines[0]["qty"],
                "unit": lines[0]["unit"],
            },
            {
                "item_code": lines[1]["item_code"],
                "item_name": lines[1]["item_name"],
                "qty": lines[1]["qty"],
                "unit": lines[1]["unit"],
            },
        ],
    }


async def render_print_design_preview(db, document_type: str, branch_id: Any = None) -> bytes:
    """Render a dummy document with the saved Design Cetakan. Read-only."""
    kind = str(document_type or "").strip().lower()
    if kind not in PREVIEW_TYPES:
        raise HTTPException(400, "Jenis preview tidak valid")

    raw_branch = str(branch_id or "").strip()
    resolved_branch = None
    if raw_branch:
        resolved_branch = await assert_known_branch_id(db, raw_branch)

    design = await get_print_design_for_pdf(db, branch_id=resolved_branch)
    if kind == "po":
        return generate_sales_order_pdf_bytes(dummy_order(), design=design)
    if kind == "invoice":
        return generate_sales_invoice_pdf_bytes(dummy_invoice(), design=design)
    if kind == "proforma":
        return generate_sales_invoice_pdf_bytes(
            dummy_invoice(),
            document_type=DOCUMENT_PROFORMA,
            design=design,
        )
    if kind == "markup":
        from services.print_design_settings import prefetch_pdf_assets
        from services.sales_invoice_markup import apply_markup_design_and_bank

        markup_design = apply_markup_design_and_bank(
            design,
            {
                "bank_name": "Bank Contoh Markup",
                "account_number": "0123456789",
                "account_holder": "Reseller Contoh",
            },
        )
        markup_design["_pdf_assets"] = await prefetch_pdf_assets(db, markup_design)
        return generate_sales_invoice_pdf_bytes(
            dummy_invoice(),
            document_type=DOCUMENT_MARKUP,
            design=markup_design,
        )
    return generate_sales_delivery_pdf_bytes(dummy_delivery(), None, design=design)


def preview_filename(document_type: str) -> str:
    kind = str(document_type or "").strip().lower()
    return PREVIEW_FILENAMES.get(kind) or "Preview_Design_Cetakan.pdf"
