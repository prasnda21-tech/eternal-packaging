"""Design Cetakan settings — presentation defaults for Eternal sales PDFs."""
from __future__ import annotations

import copy
import os
from pathlib import Path
import sys

import pytest
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_NAME", "eternal_packaging_design_cetakan_test")

from permissions import ALL_PERMISSIONS, PERMISSION_CATALOG  # noqa: E402
from services import pdf_layout as layout  # noqa: E402
from services.order_pdf import generate_sales_order_pdf_bytes  # noqa: E402
from services.print_design_settings import (  # noqa: E402
    PERM_MANAGE,
    PERM_VIEW,
    SETTINGS_KEY,
    bank_display_lines,
    default_settings,
    get_print_design_settings,
    resolve_print_design,
    save_print_design_settings,
)
from services.sales_deliveries import generate_sales_delivery_pdf_bytes  # noqa: E402
from services.sales_invoices import (  # noqa: E402
    DOCUMENT_PROFORMA,
    PROFORMA_WARNING,
    generate_sales_invoice_pdf_bytes,
)


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
    from io import BytesIO

    pages = []
    with pdfplumber.open(BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)


def test_permissions_catalog_includes_design_cetakan():
    keys = {key for _mod, key, _label in PERMISSION_CATALOG}
    assert PERM_VIEW in keys
    assert PERM_MANAGE in keys
    assert PERM_VIEW in ALL_PERMISSIONS
    assert PERM_MANAGE in ALL_PERMISSIONS
    assert ("Pengaturan", PERM_VIEW, "Lihat Design Cetakan") in PERMISSION_CATALOG
    assert ("Pengaturan", PERM_MANAGE, "Ubah Design Cetakan") in PERMISSION_CATALOG


def test_defaults_match_hardcoded_pdf_constants():
    defaults = default_settings()
    assert defaults["key"] == SETTINGS_KEY
    assert defaults["company_name"] == layout.COMPANY_NAME
    assert defaults["address_lines"] == list(layout.COMPANY_ADDRESS_LINES)
    assert defaults["accent_color"] == layout.ACCENT_COLOR_HEX
    assert tuple(bank_display_lines(defaults["invoice"]["bank_accounts"])) == layout.BANK_LINES
    assert defaults["invoice"]["estimasi_title"] == layout.ESTIMASI_TITLE
    assert defaults["invoice"]["estimasi_lines"] == list(layout.ESTIMASI_LINES)
    assert defaults["po"]["signature_labels"] == ["Operator", "Pengiriman", "Penerima"]
    assert defaults["po"]["footer_name_label"] == "Nama"
    assert defaults["surat_jalan"]["signature_labels"] == ["Gudang", "Cek Barang", "Kurir", "Penerima"]
    assert defaults["surat_jalan"]["date_label"] == "Tgl."


def test_resolve_falls_back_on_malformed_partial():
    resolved = resolve_print_design({
        "company_name": "   ",
        "accent_color": "not-a-color",
        "invoice": {"bank_accounts": "bad", "estimasi_lines": None},
        "po": {"signature_labels": ["Gudang Saja"]},
    })
    assert resolved["company_name"] == layout.COMPANY_NAME
    assert resolved["accent_color"] == layout.ACCENT_COLOR_HEX
    assert tuple(bank_display_lines(resolved["invoice"]["bank_accounts"])) == layout.BANK_LINES
    assert resolved["po"]["signature_labels"][0] == "Gudang Saja"
    assert resolved["po"]["signature_labels"][1:] == ["Pengiriman", "Penerima"]


@pytest.mark.anyio
async def test_get_returns_defaults_when_absent():
    db = MockDb()
    settings = await get_print_design_settings(db)
    assert settings["company_name"] == layout.COMPANY_NAME
    assert settings["updated_at"] is None
    assert len(db.app_settings._docs) == 0


@pytest.mark.anyio
async def test_put_persists_and_rejects_bad_color():
    db = MockDb()
    user = {"id": "u1", "email": "admin@eternalpackaging.com", "name": "Admin", "role": "admin"}
    with pytest.raises(HTTPException) as exc:
        await save_print_design_settings(db, {"accent_color": "red"}, user=user)
    assert exc.value.status_code == 400

    saved = await save_print_design_settings(
        db,
        {
            "company_name": "ETERNAL DEMO",
            "accent_color": "#112233",
            "invoice": {
                "prepared_footer": "Staff",
                "bank_accounts": [
                    {"bank_label": "BRI", "account_number": "111", "account_name": "A"},
                    {"bank_label": "BCA", "account_number": "222", "account_name": "B"},
                ],
            },
            "surat_jalan": {"signature_labels": ["Gudang Utama", "Cek", "Kurir", "Penerima"]},
        },
        user=user,
    )
    assert saved["company_name"] == "ETERNAL DEMO"
    assert saved["accent_color"] == "#112233"
    assert saved["invoice"]["prepared_footer"] == "Staff"
    assert saved["surat_jalan"]["signature_labels"][0] == "Gudang Utama"
    again = await get_print_design_settings(db)
    assert again["company_name"] == "ETERNAL DEMO"


def test_empty_design_keeps_pdf_content_equivalent_to_defaults():
    order = {
        "po_number": "PO/2026/01/01/000001",
        "customer": "PT Contoh",
        "address": "Yogyakarta",
        "order_date": "2026-01-01",
        "payment_terms": "14",
        "created_by_user": "Admin",
        "expedition": "Internal",
        "delivery_zone": "Sleman",
        "sales": "Sales A",
        "notes": "Catatan PO",
        "total_quantity": 1000,
        "line_items": [{
            "item_code": "ITM-1",
            "name": "Tas",
            "quantity": 1000,
            "unit": "pcs",
            "warna": "Hitam",
            "line_idx": 0,
        }],
        "operator_splits": [{"line_idx": 0, "operator_name": "Operator A"}],
    }
    invoice = {
        "invoice_number": "INV/2026/01/0001",
        "invoice_date": "2026-01-02",
        "due_date": "2026-01-16",
        "customer": "PT Contoh",
        "address": "Yogyakarta",
        "po_number": order["po_number"],
        "sales": "Sales A",
        "notes": "Catatan faktur",
        "subtotal": 1100000,
        "discount_amount": 0,
        "ppn_percent": 11,
        "ppn_amount": 121000,
        "paid_total": 0,
        "grand_total": 1221000,
        "lines": [{
            "item_code": "ITM-1",
            "item_name": "Tas",
            "quantity": 1000,
            "unit_price": 1100,
            "line_subtotal": 1100000,
        }],
        "other_charges": [],
    }
    delivery = {
        "sj_number": "SJ/01/01/26/001",
        "customer": "PT Contoh",
        "address": "Yogyakarta",
        "po_number": order["po_number"],
        "delivery_date": "2026-01-03",
        "created_by_name": "Admin",
        "expedition": "Internal",
        "delivery_zone": "Sleman",
        "notes": "Catatan SJ",
        "lines": [{
            "item_code": "ITM-1",
            "item_name": "Tas",
            "qty": 500,
            "unit": "pcs",
            "outlet": "Outlet A",
        }],
    }

    po_default = _pdf_text(generate_sales_order_pdf_bytes(order))
    po_none = _pdf_text(generate_sales_order_pdf_bytes(order, design=None))
    assert po_default == po_none
    assert "Operator" in po_default
    assert "Nama" in po_default

    inv_default = _pdf_text(generate_sales_invoice_pdf_bytes(invoice))
    inv_none = _pdf_text(generate_sales_invoice_pdf_bytes(invoice, design=None))
    assert inv_default == inv_none
    assert "ETERNAL PACKAGING" in inv_default
    assert "BRI : 024701002548569" in inv_default
    assert "INFO ESTIMASI PENGERJAAN" in inv_default
    assert "Disiapkan Oleh" in inv_default
    assert "BELUM DIBAYAR" not in inv_default

    proforma = _pdf_text(generate_sales_invoice_pdf_bytes(invoice, document_type=DOCUMENT_PROFORMA))
    assert "PROFORMA INVOICE" in proforma
    assert PROFORMA_WARNING in proforma
    assert "Uang dimuka" not in proforma

    sj_default = _pdf_text(generate_sales_delivery_pdf_bytes(delivery))
    sj_none = _pdf_text(generate_sales_delivery_pdf_bytes(delivery, design=None))
    assert sj_default == sj_none
    assert "SURAT JALAN" in sj_default
    assert "Gudang" in sj_default
    assert "Tgl." in sj_default


def test_custom_design_changes_labels_without_changing_titles():
    design = resolve_print_design({
        "company_name": "ETERNAL CUSTOM",
        "po": {"signature_labels": ["Ops", "Kirim", "Terima"], "footer_name_label": "Paraf"},
        "invoice": {
            "prepared_label": "Dibuat Oleh",
            "prepared_footer": "Kasir",
            "approved_label": "Dicek Oleh",
            "approved_footer": "Direktur",
            "estimasi_title": "INFO ESTIMASI CUSTOM",
        },
        "surat_jalan": {
            "signature_labels": ["Warehouse", "Checker", "Driver", "Buyer"],
            "date_label": "Tanggal",
        },
    })
    order = {
        "po_number": "PO/X",
        "customer": "Cust",
        "line_items": [{"item_code": "A", "name": "Item", "quantity": 1, "unit": "pcs", "line_idx": 0}],
        "total_quantity": 1,
    }
    invoice = {
        "invoice_number": "INV/X",
        "customer": "Cust",
        "grand_total": 1000,
        "subtotal": 1000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "paid_total": 0,
        "lines": [{"item_code": "A", "item_name": "Item", "quantity": 1, "unit_price": 1000, "line_subtotal": 1000}],
        "other_charges": [],
    }
    delivery = {
        "sj_number": "SJ/X",
        "customer": "Cust",
        "lines": [{"item_code": "A", "item_name": "Item", "qty": 1, "unit": "pcs"}],
    }

    po_text = _pdf_text(generate_sales_order_pdf_bytes(order, design=design))
    assert "Ops" in po_text and "Paraf" in po_text

    inv_text = _pdf_text(generate_sales_invoice_pdf_bytes(invoice, design=design))
    assert "ETERNAL CUSTOM" in inv_text
    assert "Dibuat Oleh" in inv_text
    assert "INFO ESTIMASI CUSTOM" in inv_text
    assert "Faktur Penjualan" in inv_text

    proforma_text = _pdf_text(
        generate_sales_invoice_pdf_bytes(invoice, document_type=DOCUMENT_PROFORMA, design=design)
    )
    assert "PROFORMA INVOICE" in proforma_text
    assert PROFORMA_WARNING in proforma_text

    sj_text = _pdf_text(generate_sales_delivery_pdf_bytes(delivery, design=design))
    assert "SURAT JALAN" in sj_text
    assert "Warehouse" in sj_text
    assert "Tanggal" in sj_text
