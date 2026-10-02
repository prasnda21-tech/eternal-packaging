"""Laporan kelompok Penjualan.

Sumber angka adalah data ERP Eternal, bukan sinkronisasi Accurate.

Penjualan (nilai barang)
- Hanya faktur penjualan yang ``financial_status``-nya finalized.
- Draft, legacy tanpa status (diperlakukan draft), dan faktur batal tidak masuk.
- Tanggal dasar adalah ``invoice_date`` (YYYY-MM-DD, inklusif). Bukan tanggal
  pesanan, pengiriman, atau pembayaran.
- Pesanan penjualan tidak dihitung sebagai omzet.
- Nilai baris = kuantitas × harga satuan, lalu diskon header faktur dialokasikan
  proporsional ke subtotal barang. Sisa pembulatan 0,01 menempel di baris terakhir
  supaya jumlah baris sama dengan ``after_discount``.
- PPN dan biaya lainnya tidak masuk kolom Penjualan. Keduanya tetap mengikuti
  angka yang sudah tersimpan di faktur; pekerjaan ini tidak mengaktifkan pajak.
- Tidak ada dokumen retur penjualan di ERP. Retur tidak direka dari faktur batal.

BPP / laba kotor
- BPP hanya dari ``inventory_issue_accounting`` berstatus posted,
  field ``accounting_posting_basis.total_cogs``.
- Dicocokkan ke baris pesanan (order_id + line_id, atau line_idx).
- Bila satu baris pesanan terbagi ke beberapa faktur finalized, BPP dibagi
  menurut kuantitas faktur. Pembulatan menempel di faktur terakhir.
- Harga beli terakhir tidak dipakai. Bila posting HPP belum ada, BPP dan laba
  dikosongkan, bukan diisi nol.

Cabang
- Jogja, Semarang, dan [Semua Cabang] mengikuti akses user.
- Cocok bila outlet/cabang faktur atau pesanannya memuat "jogja"/"yogyakarta"
  atau "semarang". Outlet lain hanya tampil pada Semua, bagi user yang boleh
  melihat Semua.
- User yang terikat satu cabang tidak dapat meminta Semua atau cabang lain.

Total
- Dihitung dari seluruh baris yang lolos filter, sebelum potongan halaman.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from services.sales_invoices import (
    FINANCIAL_FINALIZED,
    effective_financial_status,
)
from services.sales_user_mapping import is_sales_pwa_role

PERM_VIEW = "reports.sales.view"
PERM_EXPORT = "reports.sales.export"
NAV_KEY = "nav.reports.sales"

MONEY = Decimal("0.01")
MEMORIZE_COLLECTION = "sales_report_memorized"

BRANCH_JOGJA = "jogja"
BRANCH_SEMARANG = "semarang"
BRANCH_SEMUA = "semua"
BRANCH_OPTIONS = (
    {"id": BRANCH_JOGJA, "label": "Jogja"},
    {"id": BRANCH_SEMARANG, "label": "Semarang"},
    {"id": BRANCH_SEMUA, "label": "[Semua Cabang]"},
)

REF_CATEGORY = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/membuat-laporan-penjualan-perkategori-barang/"
)
REF_BPP = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/bpp-laba/"
)
REF_OPEN = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/cara-membuka-laporan-accurate-online/"
)
REF_FAKTUR = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/jumlah-faktur-penjualan/"
)
REF_MEMORIZE = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/simpan-format-laporan-modifikasi/"
)

# Names and descriptions copied from the Penjualan list in official help screenshots.
# implemented=True only when the rendered columns were also visible.
_CATALOG: Tuple[dict, ...] = (
    {"id": "penjualan-per-pelanggan", "name": "Penjualan per Pelanggan", "description": "Menampilkan daftar nilai penjualan per pelanggan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "penjualan-per-barang", "name": "Penjualan per Barang", "description": "Menampilkan daftar nilai penjualan per barang", "implemented": True, "reference": REF_CATEGORY},
    {"id": "penjualan-per-merek", "name": "Penjualan per Merek", "description": "Menampilkan daftar nilai penjualan per merk barang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "penjualan-barang-per-pelanggan", "name": "Penjualan Barang per Pelanggan", "description": "Menampilkan nilai penjualan barang per pelanggan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "penjualan-barang-per-gudang", "name": "Penjualan Barang per Gudang", "description": "Menampilkan nilai penjualan barang per gudang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "penjualan-pelanggan-per-barang", "name": "Penjualan Pelanggan per Barang", "description": "Menampilkan nilai penjualan pelanggan per barang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "histori-proses-penjualan", "name": "Histori Proses Penjualan", "description": "Menampilkan rantai proses penjualan dari penawaran hingga pembayaran", "implemented": False, "reference": REF_CATEGORY},
    {"id": "rincian-penjualan-per-pelanggan", "name": "Rincian Penjualan per Pelanggan", "description": "Menampilkan rincian nilai penjualan per pelanggan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "rincian-penjualan-per-barang", "name": "Rincian Penjualan per Barang", "description": "Menampilkan rincian nilai penjualan per barang", "implemented": True, "reference": REF_BPP},
    {"id": "penjualan-per-cabang", "name": "Penjualan per Cabang", "description": "Menampilkan daftar nilai penjualan per cabang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "penjualan-barang-per-cabang", "name": "Penjualan Barang per Cabang", "description": "Menampilkan nilai penjualan barang per cabang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "retur-penjualan-per-barang", "name": "Retur Penjualan per Barang", "description": "Menampilkan nilai retur penjualan per barang", "implemented": False, "reference": REF_CATEGORY, "gap": "ERP belum menyimpan dokumen retur penjualan. Faktur batal tidak dijadikan retur."},
    {"id": "grafik-penjualan-bulanan", "name": "Grafik Penjualan Bulanan", "description": "Menampilkan grafik batang penjualan per bulan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "porsi-penjualan-per-pelanggan", "name": "Porsi Penjualan per Pelanggan", "description": "Terlihat di daftar laporan Penjualan. Teks deskripsi pada screenshot terpotong.", "implemented": False, "reference": REF_CATEGORY, "description_partial": True},
    {"id": "porsi-penjualan-per-barang", "name": "Porsi Penjualan per Barang", "description": "Menampilkan porsi penjualan dari barang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "penawaran-per-pelanggan-belum-proses", "name": "Penawaran per Pelanggan (Belum Proses)", "description": "Menampilkan nilai penawaran penjualan yang belum di proses per pelanggan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "penawaran-per-barang-belum-proses", "name": "Penawaran per Barang (Belum Proses)", "description": "Menampilkan nilai penawaran penjualan yang belum di proses per barang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "pesanan-per-pelanggan-belum-proses", "name": "Pesanan per Pelanggan (Belum Proses)", "description": "Menampilkan nilai pesanan penjualan yang belum di proses per pelanggan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "pesanan-per-barang-belum-proses", "name": "Pesanan per Barang (Belum Proses)", "description": "Menampilkan nilai pesanan penjualan yang belum di proses per barang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "uang-muka-penjualan", "name": "Uang Muka Penjualan", "description": "Menampilkan daftar uang muka penjualan grup per pelanggan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "target-penjualan-barang", "name": "Target Penjualan Barang", "description": "Menampilkan target dan riil penjualan barang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "target-penjualan-per-kategori", "name": "Target Penjualan per Kategori", "description": "Menampilkan target dan riil penjualan per kategori barang", "implemented": False, "reference": REF_CATEGORY},
    {"id": "target-penjualan-penjual", "name": "Target Penjualan Penjual", "description": "Menampilkan capaian target dan riil penjualan dari penjual", "implemented": False, "reference": REF_CATEGORY},
    {"id": "target-penjualan-per-bulan", "name": "Target Penjualan per Bulan", "description": "Menampilkan target dan riil penjualan per bulan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "daftar-penawaran-penjualan", "name": "Daftar Penawaran Penjualan", "description": "Laporan yang berisi daftar penawaran penjualan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "rincian-penawaran-penjualan", "name": "Rincian Penawaran Penjualan", "description": "Laporan yang berisi rincian barang pada penawaran penjualan", "implemented": False, "reference": REF_CATEGORY},
    {"id": "daftar-pesanan-penjualan", "name": "Daftar Pesanan Penjualan", "description": "Laporan yang berisi daftar pesanan penjualan", "implemented": False, "reference": REF_FAKTUR},
    {"id": "rincian-pesanan-penjualan", "name": "Rincian Pesanan Penjualan", "description": "Laporan yang berisi rincian barang pada pesanan penjualan", "implemented": False, "reference": REF_FAKTUR},
    {"id": "daftar-pengiriman-pesanan", "name": "Daftar Pengiriman Pesanan", "description": "Laporan yang berisi daftar pengiriman pesanan", "implemented": False, "reference": REF_FAKTUR},
    {"id": "rincian-pengiriman-pesanan", "name": "Rincian Pengiriman Pesanan", "description": "Laporan yang berisi rincian barang pada pengiriman pesanan", "implemented": False, "reference": REF_FAKTUR},
    {"id": "status-pengiriman-pesanan", "name": "Status Pengiriman Pesanan", "description": "Laporan yang berisi daftar pengiriman pesanan", "implemented": False, "reference": REF_FAKTUR, "description_partial": True},
    {"id": "daftar-faktur-penjualan", "name": "Daftar Faktur Penjualan", "description": "Laporan yang berisi daftar faktur penjualan", "implemented": True, "reference": REF_FAKTUR},
    {"id": "rincian-faktur-penjualan", "name": "Rincian Faktur Penjualan", "description": "Laporan yang berisi rincian barang pada faktur penjualan", "implemented": False, "reference": REF_FAKTUR},
    {"id": "grafik-penjualan", "name": "Grafik Penjualan", "description": "Menampilkan nilai penjualan dalam bentuk grafik berdasarkan waktu", "implemented": False, "reference": REF_FAKTUR},
    {"id": "daftar-retur-penjualan", "name": "Daftar Retur Penjualan", "description": "Laporan yang berisi daftar retur penjualan", "implemented": False, "reference": REF_FAKTUR, "gap": "ERP belum menyimpan dokumen retur penjualan."},
    {"id": "rincian-retur-penjualan", "name": "Rincian Retur Penjualan", "description": "Laporan yang berisi rincian barang pada retur penjualan", "implemented": False, "reference": REF_FAKTUR, "gap": "ERP belum menyimpan dokumen retur penjualan."},
    {"id": "barang-klaim-pelanggan", "name": "Barang Klaim Pelanggan", "description": "Daftar barang-barang yang diklaim oleh pelanggan", "implemented": False, "reference": REF_FAKTUR},
    {"id": "penggunaan-uang-muka-penjualan", "name": "Penggunaan Uang Muka Penjualan", "description": "Laporan yang berisi daftar penggunaan uang muka dalam faktur penjualan", "implemented": False, "reference": REF_FAKTUR},
    {"id": "pembayaran-cicilan", "name": "Pembayaran Cicilan", "description": "Terlihat di daftar laporan Penjualan. Teks deskripsi pada screenshot terpotong.", "implemented": False, "reference": REF_FAKTUR, "description_partial": True},
    {"id": "cicilan-belum-terbayar", "name": "Cicilan Belum Terbayar", "description": "Terlihat di daftar laporan Penjualan. Teks deskripsi pada screenshot terpotong.", "implemented": False, "reference": REF_FAKTUR, "description_partial": True},
)

IMPLEMENTED_IDS = frozenset(row["id"] for row in _CATALOG if row["implemented"])

SUMMARY_COLUMNS = (
    {"key": "kode", "label": "Kode #", "align": "left", "default": False},
    {"key": "nama_barang", "label": "Nama Barang", "align": "left", "default": True},
    {"key": "satuan", "label": "Satuan", "align": "left", "default": True},
    {"key": "kuantitas", "label": "Kuantitas", "align": "right", "default": True, "numeric": True},
    {"key": "penjualan", "label": "Penjualan", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "nama_kategori", "label": "Nama Kategori Barang Barang & Jasa", "align": "left", "default": False},
)

DETAIL_COLUMNS = (
    {"key": "nomor", "label": "Nomor #", "align": "left", "default": True},
    {"key": "tanggal", "label": "Tanggal", "align": "left", "default": True},
    {"key": "keterangan", "label": "Keterangan", "align": "left", "default": True},
    {"key": "kuantitas", "label": "Kuantitas", "align": "right", "default": True, "numeric": True},
    {"key": "satuan", "label": "Satuan", "align": "left", "default": True},
    {"key": "harga", "label": "@Harga", "align": "right", "default": False, "numeric": True, "money": True},
    {"key": "diskon", "label": "Diskon", "align": "right", "default": False, "numeric": True, "money": True},
    {"key": "penjualan", "label": "Penjualan", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "bpp", "label": "BPP", "align": "right", "default": False, "numeric": True, "money": True},
    {"key": "laba", "label": "Laba", "align": "right", "default": False, "numeric": True, "money": True},
    {"key": "mata_uang", "label": "Negara/Nama Mata Uang", "align": "left", "default": False},
)

INVOICE_COLUMNS = (
    {"key": "nomor", "label": "Nomor #", "align": "left", "default": True},
    {"key": "tanggal", "label": "Tanggal", "align": "left", "default": True},
    {"key": "pelanggan", "label": "Pelanggan", "align": "left", "default": True},
    {"key": "total", "label": "Total", "align": "right", "default": True, "numeric": True, "money": True},
)

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")


class SalesReportError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dec(value: Any) -> Decimal:
    if value is None or value == "":
        return Decimal("0")
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal("0")


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY, rounding=ROUND_HALF_UP)


def _money_str(value: Optional[Decimal]) -> Optional[str]:
    if value is None:
        return None
    return f"{_money(value):.2f}"


def _qty_str(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    text = f"{quantized:.2f}"
    if text.endswith("00"):
        return text[:-3] if "." in text else text
    if text.endswith("0"):
        return text[:-1]
    return text


def parse_report_date(raw: Any, field_name: str) -> str:
    text = _text(raw)
    if not text:
        raise SalesReportError(400, f"{field_name} wajib diisi.")
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10] if fmt == "%Y-%m-%d" else text, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise SalesReportError(400, f"{field_name} harus dd/mm/yyyy.")


def format_period_date(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{int(day):02d} {_MONTHS[int(month) - 1]} {year}"


def format_row_date(iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"{int(day):02d} {_MONTHS[int(month) - 1]} {year}"


def canonical_branch(*values: Any) -> str:
    blob = " ".join(_text(value).lower() for value in values)
    if "jogja" in blob or "yogyakarta" in blob:
        return BRANCH_JOGJA
    if "semarang" in blob:
        return BRANCH_SEMARANG
    return ""


def _branch_tokens(raw: Any) -> set:
    found = set()
    if isinstance(raw, (list, tuple, set)):
        for item in raw:
            found |= _branch_tokens(item)
        return found
    if isinstance(raw, dict):
        found |= _branch_tokens(raw.get("id"))
        found |= _branch_tokens(raw.get("name") or raw.get("label") or raw.get("cabang"))
        return found
    token = canonical_branch(raw)
    if token:
        found.add(token)
    text = _text(raw).lower()
    if text in {BRANCH_JOGJA, BRANCH_SEMARANG, BRANCH_SEMUA, "semua", "all"}:
        found.add(BRANCH_SEMUA if text in {"semua", "all", BRANCH_SEMUA} else text)
    return found


def user_branch_options(user: Optional[dict], user_doc: Optional[dict] = None) -> List[dict]:
    """Jogja, Semarang, and Semua, narrowed by the user document when it has a branch."""
    if is_sales_pwa_role(user):
        return []
    role = _text((user or {}).get("role")).lower()
    if (user or {}).get("is_super_admin") or role == "admin":
        return [dict(row) for row in BRANCH_OPTIONS]
    doc = user_doc or {}
    raw_values = []
    for key in ("branches", "branch_ids", "cabang_ids", "allowed_branches"):
        if doc.get(key):
            raw_values.append(doc.get(key))
    for key in ("branch", "cabang", "branch_name", "branch_id", "cabang_id"):
        if doc.get(key):
            raw_values.append(doc.get(key))
    for key in ("branch", "cabang", "branch_name", "branch_id", "cabang_id"):
        if (user or {}).get(key):
            raw_values.append((user or {}).get(key))
    tokens = set()
    for value in raw_values:
        tokens |= _branch_tokens(value)
    specific = tokens & {BRANCH_JOGJA, BRANCH_SEMARANG}
    if not specific or BRANCH_SEMUA in tokens:
        return [dict(row) for row in BRANCH_OPTIONS]
    options = [dict(row) for row in BRANCH_OPTIONS if row["id"] in specific]
    if len(specific) > 1:
        options.append(dict(BRANCH_OPTIONS[2]))
    return options


def assert_branch_allowed(requested: str, options: Sequence[dict]) -> str:
    branch = _text(requested).lower() or BRANCH_SEMUA
    if branch in {"semua", "all", "[semua cabang]"}:
        branch = BRANCH_SEMUA
    allowed = {row["id"] for row in options}
    if branch not in allowed:
        raise SalesReportError(403, "Cabang ini tidak termasuk akses Anda.")
    if branch not in {BRANCH_JOGJA, BRANCH_SEMARANG, BRANCH_SEMUA}:
        raise SalesReportError(400, "Cabang tidak dikenal.")
    return branch


def catalog() -> List[dict]:
    rows = []
    for row in _CATALOG:
        item = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "implemented": bool(row["implemented"]),
            "reference": row["reference"],
            "group": "Penjualan",
            "description_partial": bool(row.get("description_partial")),
        }
        if row.get("gap"):
            item["gap"] = row["gap"]
        if row["implemented"]:
            item["parameters"] = ["tanggal_dari", "tanggal_sd", "cabang"]
            item["columns"] = _column_defs(row["id"])
        rows.append(item)
    return rows


def _column_defs(report_id: str) -> List[dict]:
    if report_id == "penjualan-per-barang":
        source = SUMMARY_COLUMNS
    elif report_id == "rincian-penjualan-per-barang":
        source = DETAIL_COLUMNS
    else:
        source = INVOICE_COLUMNS
    return [
        {"key": col["key"], "label": col["label"], "align": col["align"], "default": col["default"]}
        for col in source
    ]


def _selected_columns(report_id: str, layout: Optional[dict]) -> List[dict]:
    source = {
        "penjualan-per-barang": SUMMARY_COLUMNS,
        "rincian-penjualan-per-barang": DETAIL_COLUMNS,
        "daftar-faktur-penjualan": INVOICE_COLUMNS,
    }[report_id]
    requested = None
    if isinstance(layout, dict) and isinstance(layout.get("columns"), list) and layout.get("columns"):
        requested = {_text(key) for key in layout["columns"]}
    chosen = []
    for col in source:
        if requested is None:
            if col["default"]:
                chosen.append(dict(col))
        elif col["key"] in requested:
            chosen.append(dict(col))
    if not chosen:
        chosen = [dict(col) for col in source if col["default"]]
    analysis = _text((layout or {}).get("analysis_row"))
    if report_id == "penjualan-per-barang" and analysis == "nama_kategori_barang":
        chosen = [col for col in chosen if col["key"] not in {"nama_barang", "satuan", "kode"}]
        if not any(col["key"] == "nama_kategori" for col in chosen):
            chosen.insert(0, dict(next(col for col in SUMMARY_COLUMNS if col["key"] == "nama_kategori")))
        else:
            cat = next(col for col in chosen if col["key"] == "nama_kategori")
            chosen = [cat] + [col for col in chosen if col["key"] != "nama_kategori"]
    return chosen


def _analysis_row(layout: Optional[dict]) -> str:
    value = _text((layout or {}).get("analysis_row")) or "nama_barang"
    if value not in {"nama_barang", "nama_kategori_barang"}:
        raise SalesReportError(400, "Baris analisa tidak dikenal.")
    return value


def _page(rows: List[dict], page: int, page_size: int) -> Tuple[List[dict], dict]:
    size = min(max(int(page_size or 25), 1), 200)
    current = max(int(page or 1), 1)
    total = len(rows)
    start = (current - 1) * size
    return rows[start:start + size], {
        "page": current,
        "page_size": size,
        "filtered_count": total,
        "page_count": max((total + size - 1) // size, 1),
    }


def allocate_header_discount(subtotals: Sequence[Decimal], discount: Decimal) -> List[Decimal]:
    """Split a header discount across lines. The last line absorbs the rounding remainder."""
    discount = _money(_dec(discount))
    clean = [_money(_dec(value)) for value in subtotals]
    subtotal = _money(sum(clean, Decimal("0")))
    if discount < 0:
        discount = Decimal("0.00")
    if discount > subtotal:
        discount = subtotal
    if not clean:
        return []
    if subtotal == 0:
        return [Decimal("0.00") for _ in clean]
    shares: List[Decimal] = []
    used = Decimal("0.00")
    for index, amount in enumerate(clean):
        if index == len(clean) - 1:
            share = _money(discount - used)
        else:
            share = _money(discount * amount / subtotal)
            used += share
        shares.append(share)
    return shares


def split_cogs(total: Optional[Decimal], qtys: Sequence[Decimal]) -> List[Optional[Decimal]]:
    if total is None:
        return [None for _ in qtys]
    weights = [_dec(qty) for qty in qtys]
    base = sum(weights, Decimal("0"))
    if base <= 0:
        return [None for _ in qtys]
    amount = _money(total)
    shares: List[Optional[Decimal]] = []
    used = Decimal("0.00")
    for index, qty in enumerate(weights):
        if index == len(weights) - 1:
            shares.append(_money(amount - used))
        else:
            share = _money(amount * qty / base)
            used += share
            shares.append(share)
    return shares


def _line_subtotal(line: dict) -> Decimal:
    if line.get("line_subtotal") is not None:
        return _money(_dec(line.get("line_subtotal")))
    if line.get("line_total") is not None and line.get("discount_amount") in (None, "", 0, 0.0):
        return _money(_dec(line.get("line_total")))
    qty = _dec(line.get("quantity"))
    price = _dec(line.get("unit_price"))
    return _money(qty * price)


def _category_name(line: dict, master: Optional[dict]) -> Optional[str]:
    for source in (line, master or {}):
        if not isinstance(source, dict):
            continue
        for key in ("item_category_name", "category_name", "kategori", "item_category"):
            raw = source.get(key)
            if isinstance(raw, dict):
                raw = raw.get("name") or raw.get("nama")
            text = _text(raw)
            if text:
                return text
    return None


def _keterangan(line: dict) -> str:
    for key in ("keterangan", "description", "notes", "item_description"):
        text = _text(line.get(key))
        if text:
            return text
    return ""


async def _load_user_doc(db, user: Optional[dict]) -> dict:
    email = _text((user or {}).get("email"))
    uid = _text((user or {}).get("id") or (user or {}).get("_id"))
    query = {"email": email} if email else ({"id": uid} if uid else None)
    if not query:
        return {}
    return await db.users.find_one(query, {"_id": 0}) or {}


async def _company_name(db) -> str:
    for key in ("company", "company_profile", "hr_payroll"):
        doc = await db.app_settings.find_one({"key": key}, {"_id": 0})
        if not doc:
            continue
        for field in ("company_name", "name", "nama"):
            text = _text(doc.get(field))
            if text:
                return text
    return "Eternal Packaging"


async def _iter_docs(db, name: str, query: Optional[dict] = None) -> List[dict]:
    rows = []
    cursor = db[name].find(query or {}, {"_id": 0})
    async for doc in cursor:
        rows.append(doc)
    return rows


def _invoice_branch(invoice: dict, order: Optional[dict]) -> str:
    return canonical_branch(
        invoice.get("branch"),
        invoice.get("cabang"),
        invoice.get("branch_name"),
        invoice.get("outlet"),
        invoice.get("branch_id"),
        invoice.get("cabang_id"),
        (order or {}).get("outlet"),
        (order or {}).get("branch"),
        (order or {}).get("cabang"),
        (order or {}).get("branch_name"),
        (order or {}).get("branch_id"),
        (order or {}).get("cabang_id"),
    )


def _customer_name(invoice: dict, order: Optional[dict]) -> str:
    return _text(
        invoice.get("customer_name")
        or invoice.get("customer")
        or (order or {}).get("customer")
        or (order or {}).get("customer_name")
    )


async def _sales_context(db) -> dict:
    invoices = await _iter_docs(db, "sales_invoices")
    order_ids = {_text(row.get("po_id") or row.get("order_id")) for row in invoices}
    order_ids.discard("")
    orders = {}
    if order_ids:
        for row in await _iter_docs(db, "orders", {"id": {"$in": list(order_ids)}}):
            orders[_text(row.get("id"))] = row
    masters = await _iter_docs(db, "master_items")
    by_code = {}
    by_id = {}
    for row in masters:
        code = _text(row.get("item_code")).lower()
        if code:
            by_code[code] = row
        for key in ("id", "accurate_item_id"):
            token = _text(row.get(key))
            if token:
                by_id[token] = row
    cogs_by_line: Dict[Tuple[str, str], Decimal] = {}
    for doc in await _iter_docs(db, "inventory_issue_accounting", {"status": "posted"}):
        basis = doc.get("accounting_posting_basis") if isinstance(doc.get("accounting_posting_basis"), dict) else {}
        if basis.get("total_cogs") is None:
            continue
        order_id = _text(basis.get("order_id") or doc.get("order_id"))
        line_id = _text(basis.get("line_id") or doc.get("line_id"))
        line_idx = basis.get("line_idx")
        if line_idx is None:
            line_idx = doc.get("line_idx")
        if not order_id:
            continue
        key = (order_id, line_id or f"idx:{line_idx}")
        cogs_by_line[key] = _money(cogs_by_line.get(key, Decimal("0")) + _dec(basis.get("total_cogs")))
    return {
        "invoices": invoices,
        "orders": orders,
        "masters_by_code": by_code,
        "masters_by_id": by_id,
        "cogs_by_line": cogs_by_line,
    }


def _master_for(line: dict, ctx: dict) -> Optional[dict]:
    for key in ("master_item_id", "item_id", "accurate_item_id"):
        token = _text(line.get(key))
        if token and token in ctx["masters_by_id"]:
            return ctx["masters_by_id"][token]
    code = _text(line.get("item_code")).lower()
    if code and code in ctx["masters_by_code"]:
        return ctx["masters_by_code"][code]
    return None


def _line_key(invoice: dict, line: dict) -> Tuple[str, str]:
    order_id = _text(invoice.get("po_id") or invoice.get("order_id"))
    line_id = _text(line.get("line_id"))
    if line_id:
        return order_id, line_id
    return order_id, f"idx:{line.get('line_idx')}"


def _prepared_lines(ctx: dict, start: str, end: str, branch: str) -> Tuple[List[dict], dict]:
    """Return filtered detail lines plus COGS shares computed across all finalized invoices."""
    finalized = []
    for invoice in ctx["invoices"]:
        if effective_financial_status(invoice) != FINANCIAL_FINALIZED:
            continue
        order = ctx["orders"].get(_text(invoice.get("po_id") or invoice.get("order_id")))
        business = _text(invoice.get("invoice_date"))[:10]
        lines = [
            line for line in (invoice.get("lines") or [])
            if isinstance(line, dict) and (_dec(line.get("quantity")) != 0 or _line_subtotal(line) != 0)
        ]
        subtotals = [_line_subtotal(line) for line in lines]
        shares = allocate_header_discount(subtotals, _dec(invoice.get("discount_amount")))
        for index, line in enumerate(lines):
            qty = _dec(line.get("quantity"))
            master = _master_for(line, ctx)
            finalized.append({
                "invoice_id": invoice.get("id"),
                "invoice_number": _text(invoice.get("invoice_number")),
                "invoice_date": business,
                "customer": _customer_name(invoice, order),
                "branch": _invoice_branch(invoice, order),
                "keterangan": _keterangan(line),
                "item_code": _text(line.get("item_code") or (master or {}).get("item_code")),
                "item_name": _text(line.get("item_name") or line.get("name") or (master or {}).get("item_name")) or "—",
                "unit": _text(line.get("unit") or (master or {}).get("unit")),
                "quantity": qty,
                "unit_price": _money(_dec(line.get("unit_price"))),
                "subtotal": subtotals[index],
                "discount": shares[index] if index < len(shares) else Decimal("0.00"),
                "category": _category_name(line, master),
                "line_key": _line_key(invoice, line),
                "grand_total": _money(_dec(invoice.get("grand_total"))),
                "order_id": _text(invoice.get("po_id") or invoice.get("order_id")),
            })
    qty_by_key: Dict[Tuple[str, str], Decimal] = {}
    keys_order: Dict[Tuple[str, str], List[int]] = {}
    for index, row in enumerate(finalized):
        key = row["line_key"]
        if not key[0]:
            continue
        qty_by_key[key] = qty_by_key.get(key, Decimal("0")) + row["quantity"]
        keys_order.setdefault(key, []).append(index)
    for key, indexes in keys_order.items():
        total = ctx["cogs_by_line"].get(key)
        shares = split_cogs(total, [finalized[index]["quantity"] for index in indexes])
        for index, share in zip(indexes, shares):
            finalized[index]["bpp"] = share
    for row in finalized:
        row.setdefault("bpp", None)
        row["penjualan"] = _money(row["subtotal"] - row["discount"])
        if row["bpp"] is None:
            row["laba"] = None
        else:
            row["laba"] = _money(row["penjualan"] - row["bpp"])

    selected = []
    for row in finalized:
        if not row["invoice_date"] or row["invoice_date"] < start or row["invoice_date"] > end:
            continue
        if branch != BRANCH_SEMUA and row["branch"] != branch:
            continue
        selected.append(row)
    meta = {
        "finalized_line_count": len(finalized),
        "draft_excluded": True,
        "cancelled_excluded": True,
        "returns_source": None,
    }
    return selected, meta


def _sum_money(rows: Sequence[dict], key: str) -> Optional[Decimal]:
    present = [row[key] for row in rows if row.get(key) is not None]
    if not present and any(row.get(key) is None for row in rows):
        if key in {"bpp", "laba"} and rows and all(row.get(key) is None for row in rows):
            return None
    if not present:
        return Decimal("0.00") if key not in {"bpp", "laba"} else (None if rows else Decimal("0.00"))
    if key in {"bpp", "laba"} and any(row.get(key) is None for row in rows):
        # Partial cost: total only the known portion and flag incompleteness via None mix.
        return _money(sum(present, Decimal("0")))
    return _money(sum(present, Decimal("0")))


def _cell(column: dict, value: Any) -> Any:
    if column.get("money"):
        return _money_str(value) if value is not None else None
    if column.get("numeric") and column["key"] == "kuantitas":
        return _qty_str(_dec(value))
    return value


def _totals_payload(rows: Sequence[dict], columns: Sequence[dict]) -> dict:
    totals = {}
    bpp_complete = all(row.get("bpp") is not None for row in rows) if rows else True
    for column in columns:
        key = column["key"]
        if key == "kuantitas":
            totals[key] = _qty_str(sum((_dec(row.get("quantity")) for row in rows), Decimal("0")))
        elif key == "penjualan":
            totals[key] = _money_str(sum((row["penjualan"] for row in rows), Decimal("0.00")))
        elif key == "diskon":
            totals[key] = _money_str(sum((row["discount"] for row in rows), Decimal("0.00")))
        elif key == "total":
            totals[key] = _money_str(sum((_dec(row.get("grand_total")) for row in rows), Decimal("0.00")))
        elif key in {"bpp", "laba"}:
            if not rows or not bpp_complete:
                totals[key] = None
            else:
                totals[key] = _money_str(sum((row[key] for row in rows), Decimal("0.00")))
    totals["bpp_complete"] = bpp_complete
    totals["row_count"] = len(rows)
    return totals


def _summary_rows(lines: Sequence[dict], analysis: str) -> List[dict]:
    buckets: Dict[Tuple, dict] = {}
    order: List[Tuple] = []
    for line in lines:
        if analysis == "nama_kategori_barang":
            key = (line.get("category") or "",)
        else:
            key = (line["item_code"], line["item_name"], line["unit"])
        if key not in buckets:
            buckets[key] = {
                "item_code": line["item_code"],
                "item_name": line["item_name"],
                "unit": line["unit"],
                "category": line.get("category"),
                "quantity": Decimal("0"),
                "penjualan": Decimal("0.00"),
                "discount": Decimal("0.00"),
                "bpp_values": [],
                "lines": [],
            }
            order.append(key)
        bucket = buckets[key]
        bucket["quantity"] += line["quantity"]
        bucket["penjualan"] = _money(bucket["penjualan"] + line["penjualan"])
        bucket["discount"] = _money(bucket["discount"] + line["discount"])
        bucket["bpp_values"].append(line.get("bpp"))
        bucket["lines"].append(line)
    rows = []
    for key in order:
        bucket = buckets[key]
        if any(value is None for value in bucket["bpp_values"]):
            bpp = None
            laba = None
        else:
            bpp = _money(sum(bucket["bpp_values"], Decimal("0.00")))
            laba = _money(bucket["penjualan"] - bpp)
        rows.append({
            "kind": "line",
            "item_code": bucket["item_code"],
            "item_name": bucket["item_name"],
            "unit": bucket["unit"],
            "category": bucket["category"],
            "quantity": bucket["quantity"],
            "penjualan": bucket["penjualan"],
            "discount": bucket["discount"],
            "bpp": bpp,
            "laba": laba,
            "drill": None,
        })
    return rows


def _detail_groups(lines: Sequence[dict], show_currency_group: bool) -> List[dict]:
    groups: Dict[str, List[dict]] = {}
    order: List[str] = []
    for line in lines:
        name = line["item_name"] or "—"
        if name not in groups:
            groups[name] = []
            order.append(name)
        groups[name].append(line)
    rows: List[dict] = []
    if show_currency_group:
        rows.append({"kind": "group", "label": "Indonesian Rupiah", "level": 0})
    for name in order:
        members = groups[name]
        rows.append({"kind": "group", "label": name, "level": 1 if show_currency_group else 0})
        for line in members:
            rows.append({"kind": "line", **line, "drill": {
                "href": f"/penjualan/faktur-penjualan?q={line['invoice_number']}",
                "invoice_id": line["invoice_id"],
                "invoice_number": line["invoice_number"],
            }})
        bpp_values = [line.get("bpp") for line in members]
        bpp = None if any(value is None for value in bpp_values) else _money(sum(bpp_values, Decimal("0.00")))
        penjualan = _money(sum((line["penjualan"] for line in members), Decimal("0.00")))
        rows.append({
            "kind": "subtotal",
            "label": name,
            "quantity": sum((line["quantity"] for line in members), Decimal("0")),
            "penjualan": penjualan,
            "discount": _money(sum((line["discount"] for line in members), Decimal("0.00"))),
            "bpp": bpp,
            "laba": None if bpp is None else _money(penjualan - bpp),
        })
    return rows


def _project_summary(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for column in columns:
        key = column["key"]
        if key == "kode":
            raw = row.get("item_code") or ""
        elif key == "nama_barang":
            raw = row.get("item_name") or "—"
        elif key == "satuan":
            raw = row.get("unit") or ""
        elif key == "nama_kategori":
            raw = row.get("category") or "—"
        elif key == "kuantitas":
            raw = row.get("quantity")
        elif key == "penjualan":
            raw = row.get("penjualan")
        else:
            raw = row.get(key)
        values[key] = _cell(column, raw)
    return {"kind": row["kind"], "values": values, "drill": row.get("drill")}


def _project_detail_line(row: dict, columns: Sequence[dict]) -> dict:
    if row["kind"] == "group":
        return {"kind": "group", "label": row["label"], "level": row.get("level") or 0}
    values = {}
    for column in columns:
        key = column["key"]
        if key == "nomor":
            raw = row.get("invoice_number") if row["kind"] == "line" else ""
        elif key == "tanggal":
            raw = format_row_date(row["invoice_date"]) if row["kind"] == "line" and row.get("invoice_date") else ""
        elif key == "keterangan":
            raw = row.get("keterangan") if row["kind"] == "line" else ""
        elif key == "kuantitas":
            raw = row.get("quantity")
        elif key == "satuan":
            raw = row.get("unit") if row["kind"] == "line" else ""
        elif key == "harga":
            raw = row.get("unit_price") if row["kind"] == "line" else None
        elif key == "diskon":
            raw = row.get("discount")
        elif key == "penjualan":
            raw = row.get("penjualan")
        elif key == "bpp":
            raw = row.get("bpp")
        elif key == "laba":
            raw = row.get("laba")
        elif key == "mata_uang":
            raw = "Indonesian Rupiah" if row["kind"] == "line" else ""
        else:
            raw = None
        values[key] = _cell(column, raw) if raw is not None or column.get("money") else ("" if not column.get("numeric") else _cell(column, raw))
    payload = {"kind": row["kind"], "values": values}
    if row["kind"] == "line":
        payload["drill"] = row.get("drill")
    return payload


def _invoice_rows(lines: Sequence[dict]) -> List[dict]:
    seen = []
    index = {}
    for line in lines:
        key = line["invoice_id"] or line["invoice_number"]
        if key not in index:
            index[key] = {
                "kind": "line",
                "invoice_number": line["invoice_number"],
                "invoice_date": line["invoice_date"],
                "customer": line["customer"],
                "grand_total": line["grand_total"],
                "drill": {
                    "href": f"/penjualan/faktur-penjualan?q={line['invoice_number']}",
                    "invoice_id": line["invoice_id"],
                    "invoice_number": line["invoice_number"],
                },
            }
            seen.append(index[key])
    seen.sort(key=lambda row: (row["invoice_date"], row["invoice_number"]))
    return seen


def _project_invoice(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for column in columns:
        key = column["key"]
        if key == "nomor":
            raw = row["invoice_number"]
        elif key == "tanggal":
            raw = format_row_date(row["invoice_date"]) if row.get("invoice_date") else ""
        elif key == "pelanggan":
            raw = row["customer"] or "—"
        elif key == "total":
            raw = row["grand_total"]
        else:
            raw = None
        values[key] = _cell(column, raw)
    return {"kind": "line", "values": values, "drill": row.get("drill")}


def _methodology(branch: str, report_id: str) -> dict:
    return {
        "source": "sales_invoices.financial_status = finalized",
        "date": "invoice_date inklusif, bukan tanggal pesanan",
        "draft": "draft dan faktur tanpa status tidak masuk",
        "cancelled": "faktur batal tidak masuk dan tidak dihitung sebagai retur",
        "returns": "dokumen retur penjualan tidak ada di ERP",
        "discount": "diskon header dialokasikan ke baris menurut subtotal barang; sisa 0,01 di baris terakhir",
        "penjualan": "setelah diskon barang, sebelum PPN dan biaya lainnya",
        "invoice_total": "kolom Total pada Daftar Faktur Penjualan adalah grand_total faktur",
        "tax": "PPN tidak diaktifkan di pekerjaan ini; angka pajak yang sudah tersimpan tidak diubah",
        "bpp": "hanya accounting_posting_basis.total_cogs dari inventory_issue_accounting posted",
        "bpp_missing": "BPP dan laba dikosongkan bila posting HPP belum ada",
        "branch": branch,
        "orders_are_not_revenue": True,
        "report_id": report_id,
    }


async def run_report(
    db,
    *,
    user: Optional[dict],
    report_id: str,
    date_from: Any,
    date_to: Any,
    branch: Any = BRANCH_SEMUA,
    layout: Optional[dict] = None,
    page: int = 1,
    page_size: int = 25,
    include_all: bool = False,
) -> dict:
    if is_sales_pwa_role(user):
        raise SalesReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    spec = next((row for row in _CATALOG if row["id"] == report_id), None)
    if spec is None:
        raise SalesReportError(404, "Laporan tidak ada di daftar Penjualan yang terverifikasi.")
    if not spec["implemented"]:
        raise SalesReportError(
            422,
            "Nama laporan ini terlihat di daftar Accurate, tetapi kolom defaultnya belum terlihat di screenshot referensi sehingga angka tidak dihitung.",
        )
    start = parse_report_date(date_from, "Dari")
    end = parse_report_date(date_to, "s/d")
    if end < start:
        raise SalesReportError(400, "Tanggal s/d tidak boleh lebih kecil dari tanggal Dari.")
    user_doc = await _load_user_doc(db, user)
    options = user_branch_options(user, user_doc)
    chosen = assert_branch_allowed(_text(branch), options)
    columns = _selected_columns(report_id, layout)
    analysis = _analysis_row(layout) if report_id == "penjualan-per-barang" else ""
    show_currency = bool((layout or {}).get("group_currency"))
    ctx = await _sales_context(db)
    lines, meta = _prepared_lines(ctx, start, end, chosen)
    company = await _company_name(db)
    branch_label = next(row["label"] for row in BRANCH_OPTIONS if row["id"] == chosen)

    if report_id == "penjualan-per-barang":
        full = _summary_rows(lines, analysis)
        display_rows = [_project_summary(row, columns) for row in full]
        totals = _totals_payload(full, columns)
        grouped = False
    elif report_id == "rincian-penjualan-per-barang":
        grouped_rows = _detail_groups(lines, show_currency)
        display_rows = []
        for row in grouped_rows:
            if row["kind"] == "group":
                display_rows.append({"kind": "group", "label": row["label"], "level": row.get("level") or 0})
            else:
                display_rows.append(_project_detail_line(row, columns))
        totals = _totals_payload(lines, columns)
        full = lines
        grouped = True
    else:
        invoices = _invoice_rows(lines)
        display_rows = [_project_invoice(row, columns) for row in invoices]
        totals = {
            "total": _money_str(sum((row["grand_total"] for row in invoices), Decimal("0.00"))),
            "row_count": len(invoices),
            "bpp_complete": True,
        }
        full = invoices
        grouped = False

    data_count = len(lines) if report_id == "rincian-penjualan-per-barang" else len(full if report_id == "penjualan-per-barang" else invoices)
    if include_all:
        paged = display_rows
        page_meta = {
            "page": 1,
            "page_size": max(data_count, 1),
            "filtered_count": data_count,
            "page_count": 1,
        }
    else:
        paged, page_meta = _page(display_rows, page, page_size)
        page_meta["filtered_count"] = data_count

    gap = None
    if report_id != "daftar-faktur-penjualan" and lines and not totals.get("bpp_complete") and any(
        col["key"] in {"bpp", "laba"} for col in columns
    ):
        gap = "Sebagian baris belum punya HPP posted. BPP dan laba baris itu dikosongkan, bukan diisi dari harga beli."

    return {
        "report_id": report_id,
        "name": spec["name"],
        "description": spec["description"],
        "reference": spec["reference"],
        "company_name": company,
        "period_label": f"Dari {format_period_date(start)} s/d {format_period_date(end)}",
        "branch_label": f"Cabang : {branch_label}",
        "date_from": start,
        "date_to": end,
        "branch": chosen,
        "columns": [{"key": col["key"], "label": col["label"], "align": col["align"]} for col in columns],
        "rows": paged,
        "totals": totals,
        "totals_scope": "seluruh hasil filter, bukan halaman ini saja",
        "grouped": grouped,
        "analysis_row": analysis or None,
        "gap": gap,
        "methodology": _methodology(chosen, report_id),
        "source_meta": meta,
        **page_meta,
    }


def export_workbook(report: dict) -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = (report.get("name") or "Laporan")[:31]
    sheet["A1"] = report.get("company_name") or ""
    sheet["A2"] = report.get("name") or ""
    sheet["A3"] = report.get("period_label") or ""
    sheet["A4"] = report.get("branch_label") or ""
    headers = [col["label"] for col in report.get("columns") or []]
    keys = [col["key"] for col in report.get("columns") or []]
    header_row = 6
    for index, label in enumerate(headers, start=1):
        cell = sheet.cell(header_row, index, label)
        cell.font = Font(bold=True)
    row_index = header_row + 1
    for row in report.get("rows") or []:
        if row.get("kind") == "group":
            sheet.cell(row_index, 1, row.get("label") or "")
            sheet.cell(row_index, 1).font = Font(bold=True)
            row_index += 1
            continue
        values = row.get("values") or {}
        for index, key in enumerate(keys, start=1):
            raw = values.get(key)
            cell = sheet.cell(row_index, index, "" if raw is None else raw)
            if key in {"penjualan", "bpp", "laba", "diskon", "harga", "total", "kuantitas"} and raw not in (None, ""):
                cell.alignment = Alignment(horizontal="right")
        row_index += 1
    totals = report.get("totals") or {}
    if any(key in totals for key in keys):
        sheet.cell(row_index, 1, "Total")
        sheet.cell(row_index, 1).font = Font(bold=True)
        for index, key in enumerate(keys, start=1):
            if key in totals and totals.get(key) is not None:
                cell = sheet.cell(row_index, index, totals.get(key))
                cell.font = Font(bold=True)
                cell.alignment = Alignment(horizontal="right")
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


async def list_memorized(db, user: Optional[dict]) -> List[dict]:
    if is_sales_pwa_role(user):
        raise SalesReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    email = _text((user or {}).get("email")) or _text((user or {}).get("id"))
    rows = await _iter_docs(db, MEMORIZE_COLLECTION, {"owner": email})
    rows.sort(key=lambda row: row.get("created_at") or "", reverse=True)
    return [
        {
            "id": row.get("id"),
            "name": row.get("name"),
            "description": row.get("description") or "",
            "report_id": row.get("report_id"),
            "layout": row.get("layout") or {},
            "created_at": row.get("created_at"),
        }
        for row in rows
    ]


async def save_memorized(db, user: Optional[dict], payload: dict) -> dict:
    if is_sales_pwa_role(user):
        raise SalesReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    report_id = _text((payload or {}).get("report_id"))
    if report_id not in IMPLEMENTED_IDS:
        raise SalesReportError(400, "Format hanya dapat disimpan untuk laporan yang sudah dihitung.")
    name = _text((payload or {}).get("name"))
    if not name:
        raise SalesReportError(400, "Nama laporan memorized wajib diisi.")
    layout = (payload or {}).get("layout") if isinstance((payload or {}).get("layout"), dict) else {}
    doc = {
        "id": str(uuid.uuid4()),
        "owner": _text((user or {}).get("email")) or _text((user or {}).get("id")),
        "name": name,
        "description": _text((payload or {}).get("description")),
        "report_id": report_id,
        "layout": layout,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    await db[MEMORIZE_COLLECTION].insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def delete_memorized(db, user: Optional[dict], memorize_id: str) -> dict:
    if is_sales_pwa_role(user):
        raise SalesReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    owner = _text((user or {}).get("email")) or _text((user or {}).get("id"))
    result = await db[MEMORIZE_COLLECTION].delete_one({"id": memorize_id, "owner": owner})
    if result.deleted_count == 0:
        raise SalesReportError(404, "Format memorized tidak ditemukan.")
    return {"deleted": True, "id": memorize_id}
