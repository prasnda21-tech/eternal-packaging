"""Phase 1 sales PDF layout. Local dummy documents only. No production data."""
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path

import pdfplumber
import pytest
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

PDF_DB_NAME = "eternal_packaging_pdf_layout_test"
assert PDF_DB_NAME != "eternal_packaging"

from server import app, current_user  # noqa: E402
import server as server_mod  # noqa: E402
from services.order_pdf import generate_sales_order_pdf_bytes  # noqa: E402
from services.pdf_layout import terbilang  # noqa: E402
from services.sales_deliveries import generate_sales_delivery_pdf_bytes  # noqa: E402
from services.sales_invoices import (  # noqa: E402
    PROFORMA_WARNING,
    generate_sales_invoice_pdf_bytes,
)


ADMIN = {"id": "pdf-admin", "email": "pdf-admin@example.com", "role": "admin", "name": "Admin"}
NO_VIEW = {
    "id": "pdf-noview",
    "email": "pdf-noview@example.com",
    "role": "guest",
    "name": "No View",
    "permissions_override": {
        "po.view": False,
        "work_panel.view": False,
        "schedule.view": False,
        "screen.update": False,
        "helper.prepare": False,
        "operator.update": False,
        "packing.update": False,
        "delivery_note.view": False,
        "delivery_note.manage": False,
        "loading.update": False,
        "delivery.update": False,
        "design.view": False,
        "payment.view": False,
    },
}


def _text(data: bytes) -> str:
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _pages(data: bytes):
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        return list(pdf.pages)


def test_terbilang_required_amounts():
    assert terbilang(350000) == "Tiga ratus lima puluh ribu"
    assert terbilang(1250000) == "Satu juta dua ratus lima puluh ribu"
    assert terbilang(10000000) == "Sepuluh juta"
    assert terbilang(0) == "Nol"
    assert terbilang(11) == "Sebelas"
    assert terbilang(100) == "Seratus"
    assert terbilang(1000) == "Seribu"
    assert terbilang(2000) == "Dua ribu"


def _order(**extra):
    doc = {
        "id": "po-pdf-1",
        "po_number": "PO/2026/10/05/000001",
        "customer": "PT Contoh Panjang " + ("Nusantara " * 8),
        "address": "Jl. Contoh Alamat yang cukup panjang supaya membungkus " * 3,
        "order_date": "2026-10-05",
        "payment_terms": "TRANSFER BANK",
        "created_by_user": "nabila",
        "expedition": "OUTLET",
        "estimated_delivery": "2026-10-08",
        "delivery_zone": "JU",
        "sales": "Sari Sales",
        "notes": "SABLON WARNA PUTIH",
        "line_items": [
            {
                "line_idx": 0,
                "item_code": "100168",
                "name": "Paper Cup Double Wall",
                "quantity": 500,
                "unit": "pcs",
                "color": "HITAM",
                "notes": "CATATAN ITEM",
            }
        ],
        "operator_splits": [
            {"line_idx": 0, "operator_name": "Operator A"},
            {"line_idx": 0, "operator_name": "Operator B"},
        ],
        "total_quantity": 500,
    }
    doc.update(extra)
    return doc


def test_po_pdf_is_landscape_and_prints_order_fields():
    pdf = generate_sales_order_pdf_bytes(_order())
    assert pdf.startswith(b"%PDF-")
    pages = _pages(pdf)
    assert pages[0].width > pages[0].height
    text = _text(pdf)
    assert "PT Contoh Panjang" in text
    assert "PO/2026/10/05/000001" in text
    assert "100168" in text
    assert "Paper Cup Double Wall" in text
    assert "500" in text
    assert "pcs" in text
    assert "HITAM" in text
    assert "Operator A" in text
    assert "Operator B" in text
    assert "SABLON WARNA PUTIH" in text
    assert "CATATAN ITEM" in text
    assert "Total Kuantitas" in text
    assert "Jenis Barang" in text
    assert "Pengiriman" in text
    assert "Penerima" in text
    assert "Halaman 1 dari" in text
    assert "05/10/26" in text
    assert "2026-10-05" not in text


def test_po_pdf_leaves_unmapped_operators_blank_and_paginates():
    lines = []
    for index in range(28):
        lines.append({
            "line_idx": index,
            "item_code": f"K{index:03d}",
            "name": "Nama barang sangat panjang " * 4 + str(index),
            "quantity": 10 + index,
            "unit": "pcs",
            "color": "PUTIH",
        })
    pdf = generate_sales_order_pdf_bytes(_order(
        line_items=lines,
        operator_splits=[{"operator_name": "Tidak Dipetakan"}],
        total_quantity=sum(10 + i for i in range(28)),
        notes="Keterangan " * 40,
    ))
    pages = _pages(pdf)
    assert len(pages) >= 2
    text = _text(pdf)
    assert "Tidak Dipetakan" not in text
    assert "K000" in text
    assert "K027" in text
    assert "Halaman 1 dari" in text
    assert "Halaman 2 dari" in text
    assert "Kode Barang" in (pages[1].extract_text() or "")


def _invoice(**extra):
    doc = {
        "invoice_number": "INV/2026/10/05/000018",
        "po_number": "PO/2026/10/05/000001",
        "customer": "PT Faktur Contoh",
        "address": "Dusun Contoh yang panjang " * 4,
        "sales": "Sari Sales",
        "invoice_date": "2026-10-05",
        "due_date": "2026-10-19",
        "presentation_expedition": "OUTLET",
        "notes": "Keterangan faktur uji",
        "payment_status": "BELUM DIBAYAR",
        "lines": [{
            "item_code": "100168",
            "item_name": "Paper Cup",
            "quantity": 500,
            "unit_price": 700,
            "line_subtotal": 350000,
        }],
        "subtotal": 350000,
        "discount_amount": 0,
        "ppn_percent": 0,
        "ppn_amount": 0,
        "grand_total": 350000,
        "paid_total": 0,
        "other_charges": [],
    }
    doc.update(extra)
    return doc


def test_invoice_and_proforma_share_one_layout():
    official = generate_sales_invoice_pdf_bytes(_invoice())
    proforma = generate_sales_invoice_pdf_bytes(_invoice(), document_type="proforma")
    official_text = _text(official)
    proforma_text = _text(proforma)
    assert _pages(official)[0].width < _pages(official)[0].height
    assert "Faktur Penjualan" in official_text
    assert "PROFORMA INVOICE" not in official_text
    assert "ETERNAL PACKAGING" in official_text
    assert "Dusun Maron, Donoharjo, Kec. Ngaglik" in official_text
    assert "BRI : 024701002548569" in official_text
    assert "BCA : 8467072051" in official_text
    assert "100168" in official_text
    assert "500" in official_text
    assert "Rp 700" in official_text
    assert "Rp 350.000" in official_text
    assert "Tiga ratus lima puluh ribu" in official_text
    assert "INFO ESTIMASI PENGERJAAN" in official_text
    assert "Disiapkan Oleh" in official_text
    assert "Disetujui Oleh" in official_text
    assert "Admin" in official_text
    assert "Pengelola" in official_text
    assert "Uang dimuka" in official_text
    assert "BELUM DIBAYAR" not in official_text
    assert "PPN (0%)" in official_text
    assert "Halaman 1 dari 1" in official_text
    assert "OUTLET" in official_text
    assert "Keterangan faktur uji" in official_text
    assert "PROFORMA INVOICE" in proforma_text
    assert PROFORMA_WARNING in proforma_text
    assert "Uang dimuka" not in proforma_text
    assert "BELUM DIBAYAR" not in proforma_text
    assert "INFO ESTIMASI PENGERJAAN" in proforma_text
    assert "Tiga ratus lima puluh ribu" in proforma_text


def test_invoice_paginates_many_lines():
    lines = []
    for index in range(36):
        lines.append({
            "item_code": f"C{index:03d}",
            "item_name": "Produk faktur panjang " * 3 + str(index),
            "quantity": index + 1,
            "unit_price": 1000,
            "line_subtotal": (index + 1) * 1000,
        })
    pdf = generate_sales_invoice_pdf_bytes(_invoice(
        lines=lines,
        subtotal=sum(row["line_subtotal"] for row in lines),
        grand_total=sum(row["line_subtotal"] for row in lines),
        notes="Catatan " * 30,
    ))
    pages = _pages(pdf)
    assert len(pages) >= 2
    assert "Kode Barang" in (pages[1].extract_text() or "")
    text = _text(pdf)
    assert "Halaman 2 dari" in text


def test_delivery_pdf_uses_shipment_qty_not_a_title_from_the_po():
    pdf = generate_sales_delivery_pdf_bytes({
        "sj_number": "SJ/2026/10/05/000019",
        "po_number": "PO/2026/10/05/000001",
        "customer": "PT Kirim Contoh",
        "address": "Alamat kirim yang panjang " * 4,
        "sales": "Sari Sales",
        "expedition": "OUTLET",
        "created_by_name": "nabila",
        "delivery_date": "2026-10-05",
        "delivery_zone": "JU",
        "outlet": "Gudang Pusat",
        "notes": "SEDOTAN PARSIAL",
        "line_items": [
            {"item_code": "100001", "name": "Cup 12 oz", "qty": 2000, "unit": "pcs", "notes": "SABLON PUTIH"},
            {"item_code": "100002", "name": "Cup 16 oz", "qty": 50, "unit": "pcs"},
        ],
        "total_qty": 99999,
    })
    text = _text(pdf)
    pages = _pages(pdf)
    assert pages[0].width < pages[0].height
    assert "SURAT JALAN" in text
    assert "SJ/2026/10/05/000019" in text
    assert "PT Kirim Contoh" in text
    assert "2.000" in text
    assert "50" in text
    assert "99.999" not in text
    assert "2.050" in text
    assert "Jumlah Barang" in text
    assert "Gudang Pusat" in text
    assert "SEDOTAN PARSIAL" in text
    assert "SABLON PUTIH" in text
    assert "Gudang" in text
    assert "Cek Barang" in text
    assert "Kurir" in text
    assert "Penerima" in text
    assert "Tgl." in text
    assert "ETERNAL PACKAGING" in text
    assert "05/10/26" in text
    assert "2026-10-05" not in text
    assert "PENGIRIMAN PESANAN / SURAT JALAN" not in text


def test_delivery_pdf_paginates():
    lines = [
        {
            "item_code": f"D{index:03d}",
            "name": "Barang kirim panjang " * 4,
            "qty": 3,
            "unit": "pcs",
        }
        for index in range(32)
    ]
    pdf = generate_sales_delivery_pdf_bytes({
        "sj_number": "SJ-MULTI",
        "customer": "PT Multi",
        "delivery_date": "2026-10-05",
        "outlet": "Outlet A",
        "line_items": lines,
        "notes": "Catatan kirim " * 20,
    })
    pages = _pages(pdf)
    assert len(pages) >= 2
    assert "Kode Barang" in (pages[1].extract_text() or "")
    assert "96" in _text(pdf)


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server_mod.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server_mod.db = server_mod.client[PDF_DB_NAME]
    yield
    app.dependency_overrides.pop(current_user, None)


@pytest.mark.anyio
async def test_po_pdf_endpoint_permission(bind_motor):
    order_id = str(uuid.uuid4())
    await server_mod.db.orders.insert_one({
        "id": order_id,
        "po_number": "PO-PDF-ENDPOINT",
        "customer": "PT Endpoint",
        "line_items": [{"item_code": "ABC", "name": "Cup", "quantity": 2, "unit": "pcs"}],
        "total_quantity": 2,
    })
    try:
        app.dependency_overrides[current_user] = lambda: NO_VIEW
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            denied = await client.get(f"/api/orders/{order_id}/pdf")
        assert denied.status_code == 403
        app.dependency_overrides[current_user] = lambda: ADMIN
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            allowed = await client.get(f"/api/orders/{order_id}/pdf")
        assert allowed.status_code == 200, allowed.text
        assert allowed.content.startswith(b"%PDF")
        assert "PT Endpoint" in _text(allowed.content)
        assert "PO-PDF-ENDPOINT" in _text(allowed.content)
        stored = await server_mod.db.orders.find_one({"id": order_id}, {"_id": 0})
        assert stored["customer"] == "PT Endpoint"
        assert stored["line_items"][0]["quantity"] == 2
    finally:
        await server_mod.db.orders.delete_many({"id": order_id})
