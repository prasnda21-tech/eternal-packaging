"""Laporan Penjualan: faktur finalized, diskon, cabang, BPP posted, dan izin."""
from __future__ import annotations

import os
import sys
import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("MONGO_URL", "mongodb://127.0.0.1:27017")
_worker = os.environ.get("PYTEST_XDIST_WORKER") or "main"
os.environ["DB_NAME"] = f"eternal_packaging_sales_reports_test_{_worker}"
os.environ.setdefault("JWT_SECRET", "test-secret-key-12345678901234567890")
os.environ.setdefault("CORS_ORIGINS", "*")

import server
from permissions import ALL_PERMISSIONS, ROLE_DEFAULT_PERMISSIONS
from server import app, current_user
from services.sales_reports import allocate_header_discount

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
        "sales_invoices", "orders", "master_items", "inventory_issue_accounting",
        "users", "sales_report_memorized", "app_settings",
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


def _line(name, qty, price, *, code="BRG", unit="Pcs", line_id="L1", category=None):
    subtotal = round(qty * price, 2)
    row = {
        "line_idx": 0,
        "line_id": line_id,
        "item_name": name,
        "item_code": code,
        "quantity": qty,
        "unit": unit,
        "unit_price": price,
        "line_subtotal": subtotal,
        "line_total": subtotal,
    }
    if category:
        row["item_category_name"] = category
    return row


async def _invoice(
    *,
    number,
    date,
    lines,
    status="finalized",
    outlet="Cabang Jogja",
    customer="Pelanggan Umum",
    discount=0,
    ppn=0,
    charges=0,
    po_id="po-1",
    omit_status=False,
):
    subtotal = round(sum(line["line_subtotal"] for line in lines), 2)
    after = round(subtotal - discount, 2)
    grand = round(after + ppn + charges, 2)
    doc = {
        "id": str(uuid.uuid4()),
        "invoice_number": number,
        "invoice_date": date,
        "customer": customer,
        "outlet": outlet,
        "po_id": po_id,
        "discount_amount": discount,
        "subtotal": subtotal,
        "after_discount": after,
        "ppn_amount": ppn,
        "other_charges_total": charges,
        "grand_total": grand,
        "lines": lines,
    }
    if not omit_status:
        doc["financial_status"] = status
    await server.db.sales_invoices.insert_one(doc)
    return doc


async def _show(client, report_id, **extra):
    body = {
        "report_id": report_id,
        "date_from": "01/03/2026",
        "date_to": "31/03/2026",
        "branch": "semua",
        "page": 1,
        "page_size": 25,
    }
    body.update(extra)
    response = await client.post("/api/laporan/penjualan/tampilkan", json=body)
    return response


async def test_sales_eternal_is_not_granted_sales_reports():
    perms = set(ROLE_DEFAULT_PERMISSIONS["sales_eternal"])
    assert "reports.sales.view" not in perms
    assert "reports.sales.export" not in perms
    assert "nav.reports.sales" not in perms
    assert "reports.sales.view" in ALL_PERMISSIONS
    assert "reports.sales.export" in ALL_PERMISSIONS


async def test_discount_remainder_lands_on_last_line():
    shares = allocate_header_discount(
        [Decimal("10"), Decimal("10"), Decimal("10")],
        Decimal("1"),
    )
    assert shares == [Decimal("0.33"), Decimal("0.33"), Decimal("0.34")]
    assert sum(shares, Decimal("0")) == Decimal("1.00")


async def test_catalog_uses_verified_names_and_admin_branches():
    async with await client_for(_user()) as client:
        response = await client.get("/api/laporan/penjualan/katalog")
    assert response.status_code == 200, response.text
    body = response.json()
    names = [row["name"] for row in body["reports"]]
    assert names.count("Penjualan per Barang") == 1
    assert "Rincian Penjualan per Barang" in names
    assert "Daftar Faktur Penjualan" in names
    assert "Penjualan per Salesman" not in names
    implemented = {row["id"] for row in body["reports"] if row["implemented"]}
    assert implemented == {
        "penjualan-per-barang",
        "rincian-penjualan-per-barang",
        "daftar-faktur-penjualan",
    }
    assert [row["id"] for row in body["branches"]] == ["jogja", "semarang", "semua"]


async def test_normal_discount_return_draft_cancelled_and_dates():
    await _invoice(
        number="SI.2026.03.00001",
        date="2026-03-02",
        lines=[_line("Kursi Kantor", 1, 350000, code="KR", unit="Set", line_id="L-kursi")],
        outlet="Cabang Jogja",
        customer="CV Mitra",
    )
    await _invoice(
        number="SI.2026.03.00002",
        date="2026-03-10",
        lines=[
            _line("Snack A", 2, 10000, code="SN", unit="Box", line_id="L-a"),
            _line("Snack B", 1, 5000, code="SNB", unit="Box", line_id="L-b"),
        ],
        discount=3000,
        ppn=2200,
        charges=500,
        outlet="Cabang Semarang",
        customer="Bu Tejo",
        po_id="po-snack",
    )
    await _invoice(
        number="SI.DRAFT",
        date="2026-03-10",
        lines=[_line("Draft Item", 9, 999, code="DR", line_id="L-d")],
        status="draft",
        outlet="Cabang Jogja",
    )
    await _invoice(
        number="SI.BATAL",
        date="2026-03-10",
        lines=[_line("Batal Item", 4, 8000, code="BT", line_id="L-btl")],
        status="cancelled",
        outlet="Cabang Jogja",
    )
    await _invoice(
        number="SI.LEGACY",
        date="2026-03-10",
        lines=[_line("Legacy", 1, 1000, code="LG", line_id="L-lg")],
        omit_status=True,
        outlet="Cabang Jogja",
    )
    await _invoice(
        number="SI.LUAR",
        date="2026-02-28",
        lines=[_line("Luar Periode", 1, 7000, code="OUT", line_id="L-out")],
        outlet="Cabang Jogja",
    )
    await _invoice(
        number="SI.TEPI",
        date="2026-03-31",
        lines=[_line("Tepi Akhir", 1, 4000, code="TP", unit="Pcs", line_id="L-tp")],
        outlet="Cabang Jogja",
    )
    await server.db.orders.insert_one({
        "id": "po-only",
        "po_number": "PO-ONLY",
        "customer": "Bukan Penjualan",
        "outlet": "Cabang Jogja",
        "order_date": "2026-03-05",
        "lines": [_line("Hanya Pesanan", 20, 50000, code="PO", line_id="L-po")],
    })

    async with await client_for(_user()) as client:
        shown = await _show(client, "penjualan-per-barang", branch="semua")
    assert shown.status_code == 200, shown.text
    body = shown.json()
    by_name = {row["values"]["nama_barang"]: row["values"] for row in body["rows"] if row["kind"] == "line"}
    assert set(by_name) == {"Kursi Kantor", "Snack A", "Snack B", "Tepi Akhir"}
    assert by_name["Kursi Kantor"]["penjualan"] == "350000.00"
    assert by_name["Kursi Kantor"]["kuantitas"] == "1"
    assert by_name["Kursi Kantor"]["satuan"] == "Set"
    # 20000 and 5000, discount 3000 → 2400 and 600. Net 17600 and 4400.
    assert by_name["Snack A"]["penjualan"] == "17600.00"
    assert by_name["Snack B"]["penjualan"] == "4400.00"
    assert body["totals"]["penjualan"] == "376000.00"
    assert body["totals"]["kuantitas"] == "5"
    assert "Draft Item" not in by_name
    assert "Batal Item" not in by_name
    assert "Hanya Pesanan" not in by_name
    assert body["period_label"] == "Dari 01 Mar 2026 s/d 31 Mar 2026"

    async with await client_for(_user()) as client:
        edge = await _show(client, "penjualan-per-barang", date_from="2026-03-02", date_to="2026-03-02")
    assert edge.status_code == 200
    names = [row["values"]["nama_barang"] for row in edge.json()["rows"] if row["kind"] == "line"]
    assert names == ["Kursi Kantor"]

    async with await client_for(_user()) as client:
        invoices = await _show(client, "daftar-faktur-penjualan", branch="semua")
    assert invoices.status_code == 200
    listed = invoices.json()
    numbers = [row["values"]["nomor"] for row in listed["rows"]]
    assert "SI.2026.03.00002" in numbers
    assert "SI.DRAFT" not in numbers
    assert "SI.BATAL" not in numbers
    snack = next(row for row in listed["rows"] if row["values"]["nomor"] == "SI.2026.03.00002")
    # grand_total includes ppn 2200 and charges 500: 25000 - 3000 + 2200 + 500 = 24700
    assert snack["values"]["total"] == "24700.00"
    assert snack["values"]["tanggal"] == "10 Mar 2026"
    assert snack["values"]["pelanggan"] == "Bu Tejo"
    assert listed["totals"]["total"] == "378700.00"


async def test_branch_access_jogja_semarang_and_semua():
    await _invoice(number="J1", date="2026-03-04", lines=[_line("Barang Jogja", 1, 100, code="J", line_id="j")], outlet="Outlet Jogja")
    await _invoice(number="S1", date="2026-03-04", lines=[_line("Barang Semarang", 1, 40, code="S", line_id="s")], outlet="Semarang Pusat", po_id="po-s")
    await _invoice(number="X1", date="2026-03-04", lines=[_line("Barang Lain", 1, 7, code="X", line_id="x")], outlet="Jakarta", po_id="po-x")
    jogja_user = _user(role="holding_team", permissions_override={
        "reports.sales.view": True,
        "nav.reports.sales": True,
    }, branch="Jogja")
    async with await client_for(jogja_user) as client:
        catalog = await client.get("/api/laporan/penjualan/katalog")
        assert catalog.status_code == 200
        assert [row["id"] for row in catalog.json()["branches"]] == ["jogja"]
        own = await _show(client, "penjualan-per-barang", branch="jogja")
        denied_semarang = await _show(client, "penjualan-per-barang", branch="semarang")
        denied_all = await _show(client, "penjualan-per-barang", branch="semua")
    assert own.status_code == 200
    names = [row["values"]["nama_barang"] for row in own.json()["rows"] if row["kind"] == "line"]
    assert names == ["Barang Jogja"]
    assert denied_semarang.status_code == 403
    assert denied_all.status_code == 403

    async with await client_for(_user()) as client:
        all_rows = await _show(client, "penjualan-per-barang", branch="semua")
        only_semarang = await _show(client, "penjualan-per-barang", branch="semarang")
    assert {row["values"]["nama_barang"] for row in all_rows.json()["rows"] if row["kind"] == "line"} == {
        "Barang Jogja", "Barang Semarang", "Barang Lain",
    }
    assert [row["values"]["nama_barang"] for row in only_semarang.json()["rows"] if row["kind"] == "line"] == ["Barang Semarang"]


async def test_empty_period_and_paged_totals_cover_every_row():
    async with await client_for(_user()) as client:
        empty = await _show(client, "rincian-penjualan-per-barang")
    assert empty.status_code == 200
    body = empty.json()
    assert body["rows"] == []
    assert body["totals"]["penjualan"] == "0.00"
    assert body["totals"]["row_count"] == 0

    for index in range(30):
        await _invoice(
            number=f"SI-P{index:02d}",
            date="2026-03-15",
            lines=[_line(f"Barang {index:02d}", 1, 1000 + index, code=f"C{index}", line_id=f"L{index}")],
            outlet="Cabang Jogja",
            po_id=f"po-{index}",
        )
    async with await client_for(_user()) as client:
        page = await _show(client, "penjualan-per-barang", page=2, page_size=10)
    assert page.status_code == 200
    paged = page.json()
    assert paged["filtered_count"] == 30
    assert len([row for row in paged["rows"] if row["kind"] == "line"]) == 10
    assert paged["totals"]["penjualan"] == f"{sum(1000 + index for index in range(30)):.2f}"
    assert paged["totals"]["row_count"] == 30
    page_sum = sum(Decimal(row["values"]["penjualan"]) for row in paged["rows"] if row["kind"] == "line")
    assert page_sum != Decimal(paged["totals"]["penjualan"])


async def test_detail_subtotal_bpp_and_missing_cost_is_blank():
    await _invoice(
        number="SI.2026.03.00001",
        date="2026-03-02",
        lines=[_line("Kursi Kantor", 1, 350000, code="KR", unit="Set", line_id="L-kursi")],
        outlet="Cabang Jogja",
        po_id="po-kursi",
    )
    await _invoice(
        number="SI.2026.03.00009",
        date="2026-03-03",
        lines=[_line("Tanpa HPP", 2, 10000, code="TH", unit="Pcs", line_id="L-no")],
        outlet="Cabang Jogja",
        po_id="po-no",
    )
    await server.db.inventory_issue_accounting.insert_one({
        "status": "posted",
        "movement_id": "mov-kursi",
        "accounting_posting_basis": {
            "order_id": "po-kursi",
            "line_id": "L-kursi",
            "total_cogs": "280000.00",
        },
    })
    await server.db.inventory_issue_accounting.insert_one({
        "status": "pending",
        "movement_id": "mov-no",
        "accounting_posting_basis": {
            "order_id": "po-no",
            "line_id": "L-no",
            "total_cogs": "999.00",
        },
    })
    layout = {"columns": ["nomor", "tanggal", "keterangan", "kuantitas", "satuan", "penjualan", "bpp", "laba"]}
    async with await client_for(_user()) as client:
        shown = await _show(client, "rincian-penjualan-per-barang", layout=layout, page_size=50)
    assert shown.status_code == 200, shown.text
    body = shown.json()
    lines = [row for row in body["rows"] if row["kind"] == "line"]
    kursi = next(row for row in lines if row["values"]["nomor"] == "SI.2026.03.00001")
    assert kursi["values"]["penjualan"] == "350000.00"
    assert kursi["values"]["bpp"] == "280000.00"
    assert kursi["values"]["laba"] == "70000.00"
    assert kursi["values"]["tanggal"] == "02 Mar 2026"
    assert kursi["drill"]["href"].startswith("/penjualan/faktur-penjualan?q=")
    missing = next(row for row in lines if row["values"]["nomor"] == "SI.2026.03.00009")
    assert missing["values"]["bpp"] is None
    assert missing["values"]["laba"] is None
    assert body["totals"]["bpp"] is None
    assert body["totals"]["laba"] is None
    assert body["gap"]
    subtotals = [row for row in body["rows"] if row["kind"] == "subtotal"]
    kursi_total = next(row for row in subtotals if row["values"]["penjualan"] == "350000.00")
    assert kursi_total["values"]["bpp"] == "280000.00"
    assert kursi_total["values"]["kuantitas"] == "1"


async def test_cogs_is_split_across_finalized_invoices_not_last_purchase_price():
    await _invoice(
        number="SI-A",
        date="2026-03-01",
        lines=[_line("Barang Bagi", 1, 100, code="BG", line_id="L-share")],
        po_id="po-share",
    )
    await _invoice(
        number="SI-B",
        date="2026-04-01",
        lines=[_line("Barang Bagi", 3, 100, code="BG", line_id="L-share")],
        po_id="po-share",
    )
    await server.db.inventory_issue_accounting.insert_one({
        "status": "posted",
        "accounting_posting_basis": {
            "order_id": "po-share",
            "line_id": "L-share",
            "total_cogs": "40.00",
        },
    })
    layout = {"columns": ["nomor", "penjualan", "bpp", "laba"]}
    async with await client_for(_user()) as client:
        march = await _show(client, "rincian-penjualan-per-barang", layout=layout, date_from="01/03/2026", date_to="31/03/2026")
        april = await _show(client, "rincian-penjualan-per-barang", layout=layout, date_from="01/04/2026", date_to="30/04/2026")
    assert march.status_code == 200
    march_line = next(row for row in march.json()["rows"] if row["kind"] == "line")
    april_line = next(row for row in april.json()["rows"] if row["kind"] == "line")
    assert march_line["values"]["bpp"] == "10.00"
    assert april_line["values"]["bpp"] == "30.00"
    assert march_line["values"]["laba"] == "90.00"


async def test_category_analysis_groups_verified_column():
    await _invoice(
        number="SI-K1",
        date="2026-03-05",
        lines=[
            _line("Keripik", 4, 2500, code="KR1", unit="Pcs", line_id="a", category="Snack"),
            _line("Kue", 1, 10000, code="KR2", unit="Box", line_id="b", category="Snack"),
            _line("Teh", 2, 3000, code="TH", unit="Cup", line_id="c", category="Minuman"),
        ],
        po_id="po-kat",
    )
    async with await client_for(_user()) as client:
        shown = await _show(
            client,
            "penjualan-per-barang",
            layout={"analysis_row": "nama_kategori_barang", "columns": ["nama_kategori", "kuantitas", "penjualan"]},
        )
    assert shown.status_code == 200, shown.text
    body = shown.json()
    assert [col["label"] for col in body["columns"]] == [
        "Nama Kategori Barang Barang & Jasa",
        "Kuantitas",
        "Penjualan",
    ]
    rows = {row["values"]["nama_kategori"]: row["values"] for row in body["rows"] if row["kind"] == "line"}
    assert rows["Snack"]["kuantitas"] == "5"
    assert rows["Snack"]["penjualan"] == "20000.00"
    assert rows["Minuman"]["penjualan"] == "6000.00"
    assert body["totals"]["penjualan"] == "26000.00"


async def test_export_follows_filter_and_permission():
    await _invoice(number="SI-J", date="2026-03-02", lines=[_line("Jogja", 1, 10, code="J", line_id="j")], outlet="Jogja")
    await _invoice(number="SI-S", date="2026-03-02", lines=[_line("Semarang", 1, 25, code="S", line_id="s")], outlet="Semarang", po_id="po-s")
    viewer = _user(role="holding_team", permissions_override={"reports.sales.view": True})
    async with await client_for(viewer) as client:
        denied = await client.post("/api/laporan/penjualan/ekspor", json={
            "report_id": "penjualan-per-barang",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "jogja",
            "page_size": 1,
        })
    assert denied.status_code == 403

    async with await client_for(_user()) as client:
        exported = await client.post("/api/laporan/penjualan/ekspor", json={
            "report_id": "penjualan-per-barang",
            "date_from": "01/03/2026",
            "date_to": "31/03/2026",
            "branch": "jogja",
            "page_size": 1,
        })
    assert exported.status_code == 200, exported.text
    book = load_workbook(BytesIO(exported.content))
    sheet = book.active
    labels = [sheet.cell(7, col).value for col in range(1, 5)]
    assert "Jogja" in labels
    assert "Semarang" not in [sheet.cell(row, 1).value for row in range(1, 12)]
    assert sheet["A2"].value == "Penjualan per Barang"


async def test_sales_eternal_and_unauthorized_are_rejected():
    eternal = _user(role="sales_eternal", permissions_override={"reports.sales.view": True, "reports.sales.export": True})
    async with await client_for(eternal) as client:
        catalog = await client.get("/api/laporan/penjualan/katalog")
        shown = await _show(client, "penjualan-per-barang")
    assert catalog.status_code == 403
    assert shown.status_code == 403
    async with await client_for(_user(role="designer")) as client:
        denied = await client.get("/api/laporan/penjualan/katalog")
    assert denied.status_code == 403


async def test_unimplemented_verified_report_does_not_invent_numbers():
    await _invoice(number="SI-1", date="2026-03-02", lines=[_line("Ada", 1, 50, code="A", line_id="a")])
    async with await client_for(_user()) as client:
        response = await _show(client, "retur-penjualan-per-barang")
    assert response.status_code == 422
    assert "tidak dihitung" in response.json()["detail"]


async def test_memorize_saves_category_layout():
    user = _user()
    async with await client_for(user) as client:
        saved = await client.post("/api/laporan/penjualan/memorize", json={
            "report_id": "penjualan-per-barang",
            "name": "Penjualan per kategori",
            "description": "Baris analisa kategori barang",
            "layout": {"analysis_row": "nama_kategori_barang", "columns": ["nama_kategori", "kuantitas", "penjualan"]},
        })
        listed = await client.get("/api/laporan/penjualan/memorize")
    assert saved.status_code == 200, saved.text
    assert listed.status_code == 200
    item = listed.json()["items"][0]
    assert item["name"] == "Penjualan per kategori"
    assert item["layout"]["analysis_row"] == "nama_kategori_barang"
