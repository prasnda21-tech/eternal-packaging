"""Daftar Faktur Pembelian report: active invoices, RBAC opt-in, no writes."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_purchase_reports_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ALL_PERMISSIONS, OPT_IN_ONLY_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.purchase_reports import PERM_EXPORT, PERM_VIEW

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def bind_motor():
    from motor.motor_asyncio import AsyncIOMotorClient

    server.client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    server.db = server.client[os.environ["DB_NAME"]]


@pytest.fixture(autouse=True)
async def clean_db(bind_motor):
    names = (
        "purchase_invoices", "purchase_orders", "purchase_requests",
        "purchase_goods_receipts", "users", "purchase_report_memorized", "app_settings",
    )
    for name in names:
        await server.db[name].delete_many({})
    yield
    for name in names:
        await server.db[name].delete_many({})
    app.dependency_overrides.pop(current_user, None)


def _user(role="admin", **extra):
    uid = extra.pop("id", None) or f"u-{role}-{uuid.uuid4().hex[:8]}"
    doc = {
        "id": uid,
        "email": extra.pop("email", None) or f"{uid}@example.com",
        "name": role,
        "role": role,
        "status": "active",
        "user_id": uid,
        "permissions_override": extra.pop("permissions_override", {}),
    }
    doc.update(extra)
    return doc


async def client_for(user):
    app.dependency_overrides[current_user] = lambda: user
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


def _viewer(**extra):
    return _user(
        role="designer",
        permissions_override={PERM_VIEW: True, PERM_EXPORT: True},
        **extra,
    )


async def _invoice(**fields):
    doc = {
        "id": str(uuid.uuid4()),
        "invoice_number": fields.pop("invoice_number", f"FPB-{uuid.uuid4().hex[:6]}"),
        "invoice_date": fields.pop("invoice_date", "2026-03-10"),
        "purchase_order_id": fields.pop("purchase_order_id", "po-1"),
        "po_number": fields.pop("po_number", "PB-20260310-001"),
        "supplier": fields.pop("supplier", "Supplier A"),
        "subtotal": fields.pop("subtotal", 1000),
        "grand_total": fields.pop("grand_total", 1000),
        "cancelled": fields.pop("cancelled", False),
        "precedes_goods_receipt": fields.pop("precedes_goods_receipt", False),
        "invoice_source": fields.pop("invoice_source", "purchase_order"),
        "payment_status": fields.pop("payment_status", "UNPAID"),
        "accounting_status": fields.pop("accounting_status", "not_posted"),
    }
    doc.update(fields)
    await server.db.purchase_invoices.insert_one(doc)
    return doc


async def _show(client, **extra):
    body = {
        "report_id": "daftar-faktur-pembelian",
        "date_from": "01/03/2026",
        "date_to": "31/03/2026",
        "branch": "semua",
        "page": 1,
        "page_size": 25,
    }
    body.update(extra)
    return await client.post("/api/laporan/pembelian/tampilkan", json=body)


async def test_purchase_report_perms_opt_in_default_off():
    assert PERM_VIEW in ALL_PERMISSIONS
    assert PERM_EXPORT in ALL_PERMISSIONS
    assert PERM_VIEW in OPT_IN_ONLY_PERMISSIONS
    assert PERM_EXPORT in OPT_IN_ONLY_PERMISSIONS
    for role, perms in ROLE_DEFAULT_PERMISSIONS.items():
        assert PERM_VIEW not in perms, role
        assert PERM_EXPORT not in perms, role


async def test_admin_without_opt_in_cannot_open_purchase_report():
    async with await client_for(_user()) as client:
        catalog = await client.get("/api/laporan/pembelian/katalog")
        shown = await _show(client)
    assert catalog.status_code == 403
    assert shown.status_code == 403


async def test_active_cancelled_early_dates_tax_and_no_duplicate():
    await server.db.purchase_orders.insert_one({
        "id": "po-1",
        "po_number": "PB-20260310-001",
        "supplier": "Supplier A",
        "branch_id": "jogja",
    })
    await _invoice(
        invoice_number="FPB-ACTIVE",
        invoice_date="2026-03-10",
        subtotal=1500,
        grand_total=1500,
        receipt_id="gr-1",
        invoice_source="receipt",
    )
    await _invoice(
        invoice_number="FPB-CANCEL",
        invoice_date="2026-03-11",
        cancelled=True,
        subtotal=9999,
        grand_total=9999,
    )
    await _invoice(
        invoice_number="FPB-EARLY",
        invoice_date="2026-03-12",
        precedes_goods_receipt=True,
        subtotal=500,
        grand_total=500,
        accounting_status="posted",
    )
    await _invoice(
        invoice_number="FPB-TAX",
        invoice_date="2026-03-13",
        subtotal=1000,
        grand_total=1110,
        tax_amount=110,
    )
    # Same PO, second receipt-linked invoice — each invoice once (not PO-duplicated).
    await _invoice(
        invoice_number="FPB-SECOND",
        invoice_date="2026-03-14",
        purchase_order_id="po-1",
        po_number="PB-20260310-001",
        receipt_id="gr-2",
        invoice_source="receipt",
        subtotal=200,
        grand_total=200,
    )
    # Outside period
    await _invoice(invoice_number="FPB-OLD", invoice_date="2026-02-01", subtotal=50, grand_total=50)

    async with await client_for(_viewer()) as client:
        shown = await _show(client)
        edge = await _show(client, date_from="2026-03-12", date_to="2026-03-12")
        export = await client.post("/api/laporan/pembelian/ekspor", json={
            "report_id": "daftar-faktur-pembelian",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "semua",
            "include_all": True,
        })
        page = await _show(client, page=1, page_size=2)

    assert shown.status_code == 200, shown.text
    body = shown.json()
    numbers = [r["values"]["nomor"] for r in body["rows"]]
    assert "FPB-ACTIVE" in numbers
    assert "FPB-EARLY" in numbers
    assert "FPB-TAX" in numbers
    assert "FPB-SECOND" in numbers
    assert "FPB-CANCEL" not in numbers
    assert "FPB-OLD" not in numbers
    assert body["filtered_count"] == 4
    assert Decimal(body["totals"]["total"]) == Decimal("3310.00")
    # Tax incomplete across rows → total pajak null (not fabricated sum of zeros)
    assert body["totals"]["pajak"] is None
    tax_row = next(r["values"] for r in body["rows"] if r["values"]["nomor"] == "FPB-TAX")
    assert Decimal(tax_row["pajak"]) == Decimal("110.00")
    missing_tax = next(r["values"] for r in body["rows"] if r["values"]["nomor"] == "FPB-ACTIVE")
    assert missing_tax["pajak"] in (None, "")
    early = next(r["values"] for r in body["rows"] if r["values"]["nomor"] == "FPB-EARLY")
    assert "mendahului" in (early["indikator_awal"] or "").casefold()
    assert early["status_posting"] == "posted"

    assert edge.status_code == 200
    assert edge.json()["filtered_count"] == 1
    assert edge.json()["rows"][0]["values"]["nomor"] == "FPB-EARLY"

    assert export.status_code == 200
    assert export.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    assert page.json()["page_size"] == 2
    assert page.json()["filtered_count"] == 4
    assert len(page.json()["rows"]) == 2

    # Read-only: no accidental cancel stamps / mutations from report GET/POST
    docs = await server.db.purchase_invoices.find({}, {"_id": 0, "cancelled": 1, "invoice_number": 1}).to_list(20)
    active = [d for d in docs if d["invoice_number"] == "FPB-ACTIVE"][0]
    assert active.get("cancelled") is False


async def test_purchase_invoices_view_is_not_bypass():
    user = _user(
        role="designer",
        permissions_override={"purchase_invoices.view": True, "purchase_invoices.manage": True},
    )
    async with await client_for(user) as client:
        catalog = await client.get("/api/laporan/pembelian/katalog")
        shown = await _show(client)
    assert catalog.status_code == 403
    assert shown.status_code == 403


async def test_po_without_invoice_excluded():
    await server.db.purchase_orders.insert_one({
        "id": "po-orphan",
        "po_number": "PB-ORPHAN",
        "supplier": "No Invoice",
    })
    await server.db.purchase_goods_receipts.insert_one({
        "id": "gr-orphan",
        "receipt_number": "GR-ORPHAN",
        "purchase_order_id": "po-orphan",
    })
    async with await client_for(_viewer()) as client:
        shown = await _show(client)
    assert shown.status_code == 200
    assert shown.json()["filtered_count"] == 0
    assert shown.json()["rows"] == []


async def test_purchase_search_filter_before_pagination_and_export_parity():
    await server.db.purchase_orders.insert_one({
        "id": "po-1", "po_number": "PB-20260310-001", "supplier": "Supplier A", "branch_id": "jogja",
    })
    await _invoice(invoice_number="FPB-ACTIVE", invoice_date="2026-03-10", subtotal=1500, grand_total=1500)
    await _invoice(invoice_number="FPB-TAX", invoice_date="2026-03-13", subtotal=1000, grand_total=1110, tax_amount=110)
    await _invoice(invoice_number="FPB-SECOND", invoice_date="2026-03-14", subtotal=200, grand_total=200)

    async with await client_for(_viewer()) as client:
        searched = await _show(client, q="FPB-TAX")
        page1 = await _show(client, page=1, page_size=1)
        page2 = await _show(client, page=2, page_size=1)
        exported = await client.post("/api/laporan/pembelian/ekspor", json={
            "report_id": "daftar-faktur-pembelian",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "semua",
            "q": "FPB-TAX",
            "include_all": True,
        })

    assert searched.status_code == 200
    assert searched.json()["filtered_count"] == 1
    assert searched.json()["rows"][0]["values"]["nomor"] == "FPB-TAX"
    assert Decimal(searched.json()["totals"]["total"]) == Decimal("1110.00")
    # Totals stable across pages (full filtered set)
    assert page1.json()["totals"]["total"] == page2.json()["totals"]["total"]
    assert page1.json()["filtered_count"] == page2.json()["filtered_count"] == 3
    assert exported.status_code == 200
    from openpyxl import load_workbook
    from io import BytesIO
    book = load_workbook(BytesIO(exported.content))
    sheet = book.active
    numbers = [sheet.cell(r, 2).value for r in range(7, sheet.max_row + 1) if sheet.cell(r, 2).value and sheet.cell(r, 2).value != "Total"]
    assert numbers == ["FPB-TAX"]


async def test_purchase_branch_scope_cannot_bypass():
    locked = _user(
        role="holding_team",
        permissions_override={PERM_VIEW: True, PERM_EXPORT: True},
        branch="Jogja",
    )
    await server.db.users.insert_one(locked)
    await server.db.purchase_orders.insert_one({"id": "po-j", "po_number": "PB-J", "supplier": "S", "branch_id": "jogja"})
    await _invoice(invoice_number="FPB-J", purchase_order_id="po-j", invoice_date="2026-03-10", branch_id="jogja")
    async with await client_for(locked) as client:
        denied = await _show(client, branch="semarang")
        denied_all = await _show(client, branch="semua")
        ok = await _show(client, branch="jogja")
    assert denied.status_code == 403
    assert denied_all.status_code == 403
    assert ok.status_code == 200


def _line(**fields):
    doc = {
        "line_id": fields.pop("line_id", str(uuid.uuid4())),
        "master_item_id": fields.pop("master_item_id", "m-1"),
        "accurate_item_id": fields.pop("accurate_item_id", "a-1"),
        "item_code": fields.pop("item_code", "BRG-1"),
        "item_name": fields.pop("item_name", "Barang Satu"),
        "unit": fields.pop("unit", "pcs"),
        "invoice_qty": fields.pop("invoice_qty", 10),
        "quantity": fields.pop("quantity", fields.get("invoice_qty", 10) if "invoice_qty" not in fields else 10),
        "unit_price": fields.pop("unit_price", 100),
        "line_subtotal": fields.pop("line_subtotal", None),
    }
    if doc["line_subtotal"] is None:
        doc["line_subtotal"] = float(doc["invoice_qty"]) * float(doc["unit_price"])
    doc.update(fields)
    return doc


async def test_pembelian_per_barang_identity_uom_recon_export():
    await server.db.purchase_orders.insert_one({
        "id": "po-1", "po_number": "PB-1", "supplier": "Supplier A", "branch_id": "jogja",
    })
    # Same name, different IDs → separate groups
    await _invoice(
        invoice_number="FPB-PCS",
        invoice_date="2026-03-10",
        subtotal=3000,
        grand_total=3000,
        receipt_id="gr-1",
        invoice_source="receipt",
        lines=[
            _line(master_item_id="m-a", accurate_item_id="a-a", item_code="A", item_name="Same Name", unit="pcs", invoice_qty=10, unit_price=100, line_subtotal=1000),
            _line(master_item_id="m-b", accurate_item_id="a-b", item_code="B", item_name="Same Name", unit="pcs", invoice_qty=20, unit_price=100, line_subtotal=2000),
        ],
    )
    # Mixed UOM on one item + early invoice
    await _invoice(
        invoice_number="FPB-MIX",
        invoice_date="2026-03-12",
        precedes_goods_receipt=True,
        subtotal=500,
        grand_total=500,
        lines=[
            _line(master_item_id="m-a", accurate_item_id="a-a", item_code="A", item_name="Same Name", unit="box", invoice_qty=5, unit_price=100, line_subtotal=500),
        ],
    )
    # Cancelled excluded; weak identity separate; missing line_subtotal with qty*price fallback
    await _invoice(
        invoice_number="FPB-CANCEL",
        invoice_date="2026-03-11",
        cancelled=True,
        subtotal=999,
        grand_total=999,
        lines=[_line(line_subtotal=999, invoice_qty=1, unit_price=999)],
    )
    await _invoice(
        invoice_number="FPB-WEAK",
        invoice_date="2026-03-13",
        subtotal=250,
        grand_total=250,
        lines=[{
            "line_id": "weak-1",
            "item_name": "Tanpa ID",
            "unit": "pcs",
            "invoice_qty": 5,
            "unit_price": 50,
            "line_subtotal": 250,
        }],
    )
    # Second receipt on same PO — separate invoice, contributes once
    await _invoice(
        invoice_number="FPB-R2",
        invoice_date="2026-03-14",
        purchase_order_id="po-1",
        receipt_id="gr-2",
        invoice_source="receipt",
        subtotal=100,
        grand_total=100,
        lines=[
            _line(master_item_id="m-c", accurate_item_id="a-c", item_code="C", item_name="Lain", unit="pcs", invoice_qty=1, unit_price=100, line_subtotal=100),
        ],
    )
    await server.db.purchase_orders.insert_one({"id": "po-orphan", "po_number": "PB-ORPHAN", "supplier": "X"})

    async with await client_for(_viewer()) as client:
        wave1 = await _show(client)
        items = await _show(client, report_id="pembelian-per-barang")
        page1 = await _show(client, report_id="pembelian-per-barang", page=1, page_size=2)
        page2 = await _show(client, report_id="pembelian-per-barang", page=2, page_size=2)
        exported = await client.post("/api/laporan/pembelian/ekspor", json={
            "report_id": "pembelian-per-barang",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "semua",
            "include_all": True,
        })
        denied = await client.post("/api/laporan/pembelian/tampilkan", json={
            "report_id": "pembelian-per-barang",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "semarang",
        })

    assert wave1.status_code == 200
    assert items.status_code == 200, items.text
    body = items.json()
    assert body["report_id"] == "pembelian-per-barang"
    assert body["source_meta"]["reconciliation_status"] == "PASS"
    assert Decimal(body["source_meta"]["wave1_subtotal"]) == Decimal(wave1.json()["totals"]["subtotal"])
    assert Decimal(body["totals"]["nilai_pembelian"]) == Decimal(wave1.json()["totals"]["subtotal"])

    names = [(r["values"]["nama_barang"], r["values"]["identitas_barang"] if "identitas_barang" in r["values"] else None) for r in body["rows"]]
    # default layout may hide identitas — check via codes
    codes = {r["values"]["kode_barang"]: r["values"] for r in body["rows"]}
    assert "A" in codes and "B" in codes
    assert codes["A"]["nama_barang"] == codes["B"]["nama_barang"] == "Same Name"
    # Mixed UOM on A (pcs + box) → qty incomplete
    assert codes["A"]["kuantitas"] in (None, "")
    assert "satuan historis" in (codes["A"]["kelengkapan"] or "").casefold()
    assert Decimal(codes["A"]["nilai_pembelian"]) == Decimal("1500.00")
    # PCS verified still counts PCS portion
    assert Decimal(str(codes["A"]["kuantitas_pcs"])) == Decimal("10")
    # Weak identity present
    weak = [r for r in body["rows"] if "Belum Terverifikasi" in (r["values"].get("kelengkapan") or "") or r["values"].get("nama_barang") == "Tanpa ID"]
    assert weak
    assert "FPB-CANCEL" not in str(body)
    assert page1.json()["totals"]["nilai_pembelian"] == page2.json()["totals"]["nilai_pembelian"]
    assert exported.status_code == 200
    # Branch locked user path tested separately; default viewer may allow semua — deny only if scoped
    # Read-only
    docs = await server.db.purchase_invoices.find({"invoice_number": "FPB-PCS"}, {"_id": 0, "cancelled": 1}).to_list(5)
    assert docs[0].get("cancelled") is False


async def test_pembelian_per_barang_incomplete_recon_and_no_master_conversion():
    await server.db.purchase_orders.insert_one({"id": "po-1", "po_number": "PB-1", "supplier": "S", "branch_id": "jogja"})
    await _invoice(
        invoice_number="FPB-NOLINE",
        invoice_date="2026-03-10",
        subtotal=400,
        grand_total=400,
        lines=[],
    )
    await _invoice(
        invoice_number="FPB-OK",
        invoice_date="2026-03-11",
        subtotal=100,
        grand_total=100,
        lines=[_line(invoice_qty=1, unit_price=100, line_subtotal=100, unit="kg")],
    )
    async with await client_for(_viewer()) as client:
        items = await _show(client, report_id="pembelian-per-barang")
    assert items.status_code == 200
    body = items.json()
    assert body["source_meta"]["reconciliation_status"] == "INCOMPLETE"
    assert Decimal(body["source_meta"]["unreconciled_amount"]) == Decimal("400.00")
    assert Decimal(body["totals"]["nilai_pembelian"]) == Decimal("100.00")
    row = body["rows"][0]["values"]
    assert row["kuantitas_pcs"] in (None, "")
    assert Decimal(str(row["kuantitas"])) == Decimal("1")
    assert row["satuan"] == "kg"


async def test_pembelian_per_supplier_name_key_rename_tax_recon():
    await server.db.purchase_orders.insert_one({"id": "po-1", "po_number": "PB-1", "supplier": "PT Lama", "branch_id": "jogja"})
    await _invoice(invoice_number="FPB-1", invoice_date="2026-03-10", supplier="PT Lama", subtotal=1000, grand_total=1000)
    await _invoice(invoice_number="FPB-2", invoice_date="2026-03-11", supplier="PT Lama", subtotal=500, grand_total=550, tax_amount=50)
    # Rename → separate group
    await _invoice(invoice_number="FPB-3", invoice_date="2026-03-12", supplier="PT Baru", subtotal=200, grand_total=200)
    # Same normalized key (spacing/case)
    await _invoice(invoice_number="FPB-4", invoice_date="2026-03-13", supplier="  pt lama ", subtotal=100, grand_total=100)
    # Empty supplier on invoice AND PO → Tanpa Supplier (Wave 1 fallback to PO name otherwise)
    await server.db.purchase_orders.insert_one({"id": "po-empty", "po_number": "PB-E", "supplier": "", "branch_id": "jogja"})
    await _invoice(
        invoice_number="FPB-5",
        invoice_date="2026-03-14",
        purchase_order_id="po-empty",
        po_number="PB-E",
        supplier="",
        subtotal=50,
        grand_total=50,
    )
    await _invoice(invoice_number="FPB-CX", invoice_date="2026-03-15", supplier="X", cancelled=True, subtotal=9, grand_total=9)
    # Early included; multi invoice same PO
    await _invoice(
        invoice_number="FPB-EARLY",
        invoice_date="2026-03-16",
        supplier="Early Co",
        precedes_goods_receipt=True,
        subtotal=25,
        grand_total=25,
        purchase_order_id="po-1",
        receipt_id="gr-9",
        invoice_source="receipt",
    )

    async with await client_for(_viewer()) as client:
        wave1 = await _show(client)
        shown = await _show(client, report_id="pembelian-per-supplier")
        page1 = await _show(client, report_id="pembelian-per-supplier", page=1, page_size=2)
        page2 = await _show(client, report_id="pembelian-per-supplier", page=2, page_size=2)
        exported = await client.post("/api/laporan/pembelian/ekspor", json={
            "report_id": "pembelian-per-supplier",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "semua",
            "include_all": True,
        })

    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert "nama faktur" in (body.get("gap") or "").casefold()
    by_name = {r["values"]["supplier"]: r["values"] for r in body["rows"]}
    assert by_name["PT Lama"]["jumlah_faktur"] == 3  # FPB-1,2,4
    assert Decimal(by_name["PT Lama"]["subtotal"]) == Decimal("1600.00")
    assert by_name["PT Lama"]["pajak"] in (None, "")  # incomplete tax across group
    assert Decimal(by_name["PT Baru"]["subtotal"]) == Decimal("200.00")
    assert "Tanpa Supplier" in by_name
    assert Decimal(by_name["Tanpa Supplier"]["subtotal"]) == Decimal("50.00")
    assert "Early Co" in by_name
    assert body["source_meta"]["reconciliation_status"] == "PASS"
    assert Decimal(body["totals"]["subtotal"]) == Decimal(wave1.json()["totals"]["subtotal"])
    assert Decimal(body["totals"]["total"]) == Decimal(wave1.json()["totals"]["total"])
    assert body["totals"]["pajak"] is None  # incomplete across all
    assert page1.json()["totals"]["subtotal"] == page2.json()["totals"]["subtotal"]
    assert exported.status_code == 200
    from openpyxl import load_workbook
    from io import BytesIO
    book = load_workbook(BytesIO(exported.content))
    assert book.active["A2"].value == "Pembelian per Supplier"


async def test_wave2_rbac_branch_and_catalog():
    locked = _user(
        role="holding_team",
        permissions_override={PERM_VIEW: True, PERM_EXPORT: True},
        branch="Jogja",
    )
    await server.db.users.insert_one(locked)
    await server.db.purchase_orders.insert_one({"id": "po-j", "po_number": "PB-J", "supplier": "S", "branch_id": "jogja"})
    await _invoice(
        invoice_number="FPB-J",
        purchase_order_id="po-j",
        invoice_date="2026-03-10",
        branch_id="jogja",
        supplier="S",
        subtotal=10,
        grand_total=10,
        lines=[_line(invoice_qty=1, unit_price=10, line_subtotal=10)],
    )
    async with await client_for(locked) as client:
        catalog = await client.get("/api/laporan/pembelian/katalog")
        deny_item = await _show(client, report_id="pembelian-per-barang", branch="semarang")
        deny_sup = await _show(client, report_id="pembelian-per-supplier", branch="semua")
        ok_item = await _show(client, report_id="pembelian-per-barang", branch="jogja")
        ok_sup = await _show(client, report_id="pembelian-per-supplier", branch="jogja")
        export_ok = await client.post("/api/laporan/pembelian/ekspor", json={
            "report_id": "pembelian-per-barang",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "jogja",
            "include_all": True,
        })
    assert catalog.status_code == 200
    ids = [r["id"] for r in catalog.json()["reports"] if r.get("implemented")]
    assert "pembelian-per-barang" in ids and "pembelian-per-supplier" in ids
    assert "daftar-faktur-pembelian" in ids
    assert deny_item.status_code == 403
    assert deny_sup.status_code == 403
    assert ok_item.status_code == 200
    assert ok_sup.status_code == 200
    assert export_ok.status_code == 200

    no_export = _user(role="designer", permissions_override={PERM_VIEW: True, PERM_EXPORT: False})
    async with await client_for(no_export) as client:
        denied_export = await client.post("/api/laporan/pembelian/ekspor", json={
            "report_id": "pembelian-per-supplier",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "semua",
            "include_all": True,
        })
    assert denied_export.status_code == 403
