"""Laporan kelompok Penjualan.

Sumber angka adalah data ERP Eternal, bukan sinkronisasi Accurate.

Penjualan (nilai barang)
- Hanya faktur penjualan yang ``financial_status``-nya finalized.
- Draft, legacy tanpa status (diperlakukan draft), dan faktur batal tidak masuk.
- Tanggal dasar adalah ``invoice_date`` (YYYY-MM-DD, inklusif). Bukan tanggal
  pesanan, pengiriman, atau pembayaran.
- Pesanan penjualan tidak dihitung sebagai omzet.
- Dasar alokasi diskon header adalah nilai barang setelah diskon baris.
  Diskon baris tidak dipotong lagi bila ``line_subtotal`` sudah menampungnya.
- Sisa pembulatan 0,01 menempel di baris terakhir. Bila faktur menyimpan
  ``after_discount`` dan tidak ada diskon baris di luar subtotal, jumlah
  kolom Penjualan disamakan dengan ``after_discount``.
- PPN dan biaya lainnya tidak masuk kolom Penjualan. Daftar Faktur memakai
  ``grand_total`` dokumen, jadi totalnya boleh berbeda.
- Tidak ada dokumen retur penjualan di ERP. Retur tidak direka dari faktur batal.

BPP / laba kotor
- BPP hanya dari ``inventory_issue_accounting`` berstatus posted,
  field ``accounting_posting_basis.total_cogs``. Dokumen yang ``movement_id``-nya
  sama dihitung sekali. Status pending diabaikan. Harga beli terakhir tidak dipakai.
- Dicocokkan ke baris sumber (order_id + line_id, atau line_idx bila line_id kosong).
- Alokasi hanya dilakukan bila kuantitas sumber (``base_qty`` / ``issue_base_qty``)
  positif, satuan atau faktor konversi pada baris faktur sama dengan dasar
  posting, dan bagian yang diberikan tidak melebihi sisa kuantitas posting.
- Bagian faktur = total BPP × kuantitas baris / kuantitas posting.
  Barang yang belum difaktur tetap memegang sisa biaya; sisa itu tidak
  dibebankan ke faktur finalized yang sudah terlihat. Sisa pembulatan juga
  tidak ditambahkan ke faktur terakhir.
- Bila kuantitas posting tidak diketahui, satuan posting tidak konsisten, atau
  satuan baris tidak dapat dibuktikan, BPP dan laba baris itu dikosongkan.
  Total BPP dan laba dikosongkan bila ada baris dalam cakupan yang belum lengkap.
- Hasil alokasi dihitung dari seluruh faktur finalized baris itu, sebelum
  filter tanggal, cabang, dan halaman. Urutannya tanggal dan nomor faktur,
  bukan urutan dokumen dibaca.

Cabang
- Identitas terstruktur ``branch_id`` / ``cabang_id`` / ``_branch_id`` dipakai
  lebih dulu. Nilai yang tidak dikenali tidak dijatuhkan ke Jogja atau Semarang.
- Tanpa identitas terstruktur, hanya teks yang ada di peta eksplisit
  (Jogja, Yogyakarta, Semarang, dan label cabang/outlet yang sama persis).
- Semua menampilkan cabang yang boleh diakses user, termasuk data yang
  tidak dikenali hanya bagi user yang tidak dikunci ke satu cabang.
- User yang terikat satu cabang tidak dapat meminta Semua atau cabang lain.

Kategori
- Dari kategori baris atau master item yang sudah tersimpan. Kosong menjadi
  Tanpa Kategori. Laporan tidak mengarang kategori.
- Kuantitas laporan dinormalisasi ke PCS (lihat di bawah). Analisa kategori
  menjumlahkan kuantitas PCS yang terverifikasi.

Kuantitas laporan (PCS)
- Semua kuantitas pada Penjualan per Barang dan Rincian dinormalisasi ke PCS.
- Prioritas bukti: snapshot baris faktur saja (``base_qty`` + ``base_uom`` PCS,
  atau ``conversion_factor`` dengan ``base_uom`` PCS pada baris).
- ``product_unit_settings`` / master terkini TIDAK dipakai untuk mengisi
  konversi historis — pengaturan itu tidak berversi/tanggal efektif dan
  dapat mengubah angka laporan lama bila faktor master berubah.
- Transaksi yang sudah PCS memakai kuantitas transaksi (tanpa double-convert
  bila ``base_qty`` PCS konsisten).
- Faktor nol/negatif/kosong, snapshot tidak lengkap, atau ``base_qty`` vs
  faktor tidak konsisten → kuantitas belum terverifikasi; tidak dijumlahkan
  ke total PCS.
- Penjualan/diskon/BPP/laba tidak diubah oleh normalisasi ini.

Total
- Dihitung dari seluruh baris yang lolos filter, sebelum potongan halaman.
- Total kuantitas dikosongkan bila ada kuantitas PCS yang belum terverifikasi.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any, Dict, List, Optional, Sequence, Tuple

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font

from services.customer_identity import (
    display_customer_name,
    extract_stable_customer_id,
)
from services.sales_invoices import (
    FINANCIAL_FINALIZED,
    effective_financial_status,
)
from services.sales_user_mapping import (
    SALES_PWA_ROLE,
    exact_sales_label,
    is_sales_pwa_role,
    load_mapping_doc,
)

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
REF_CUSTOMER = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/membuat-laporan-penjualan-perpelanggan/"
)
REF_MEMORIZE = (
    "https://help.accurate.id/product/accurate-online/fitur-aol/laporan/"
    "daftar-laporan/simpan-format-laporan-modifikasi/"
)

# Only reports whose columns and totals can be calculated from current ERP documents.
_CATALOG: Tuple[dict, ...] = (
    {"id": "penjualan-per-barang", "name": "Penjualan per Barang", "description": "Menampilkan daftar nilai penjualan per barang", "implemented": True, "reference": REF_CATEGORY},
    {"id": "rincian-penjualan-per-barang", "name": "Rincian Penjualan per Barang", "description": "Menampilkan rincian nilai penjualan per barang", "implemented": True, "reference": REF_BPP},
    {"id": "daftar-faktur-penjualan", "name": "Daftar Faktur Penjualan", "description": "Laporan yang berisi daftar faktur penjualan", "implemented": True, "reference": REF_FAKTUR},
    {"id": "penjualan-per-pelanggan", "name": "Penjualan per Pelanggan", "description": "Nilai penjualan finalized per customer_id kanonis", "implemented": True, "reference": REF_CUSTOMER},
    {
        "id": "penjualan-per-sales",
        "name": "Penjualan per Sales",
        "description": "Nilai penjualan finalized per Mapping Sales Accurate (saat ini)",
        "implemented": True,
        "reference": REF_CUSTOMER,
    },
)

NO_CATEGORY = "Tanpa Kategori"
NO_CUSTOMER_ID = "__tanpa_id_pelanggan__"
NO_CUSTOMER_LABEL = "Tanpa ID Pelanggan"
UNVERIFIED_SALES_ID = "__belum_terverifikasi__"
UNVERIFIED_SALES_LABEL = "Belum Terverifikasi"
ATTR_MAPPED = "mapped_current"
ATTR_UNVERIFIED = "unverified"
ATTR_ALL = "all"
_INACTIVE_USER_STATUSES = frozenset({"inactive", "nonaktif", "disabled", "non-aktif"})

# Exact labels only. A longer outlet name that merely contains the city is not a branch.
_BRANCH_TEXT = {
    "jogja": BRANCH_JOGJA,
    "cabang jogja": BRANCH_JOGJA,
    "outlet jogja": BRANCH_JOGJA,
    "yogyakarta": BRANCH_JOGJA,
    "cabang yogyakarta": BRANCH_JOGJA,
    "semarang": BRANCH_SEMARANG,
    "cabang semarang": BRANCH_SEMARANG,
    "outlet semarang": BRANCH_SEMARANG,
}

IMPLEMENTED_IDS = frozenset(row["id"] for row in _CATALOG if row["implemented"])

SUMMARY_COLUMNS = (
    {"key": "kode", "label": "Kode #", "align": "left", "default": False},
    {"key": "nama_barang", "label": "Nama Barang", "align": "left", "default": True},
    {"key": "satuan", "label": "Satuan", "align": "left", "default": True},
    {"key": "kuantitas", "label": "Kuantitas (PCS)", "align": "right", "default": True, "numeric": True},
    {"key": "penjualan", "label": "Penjualan", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "nama_kategori", "label": "Nama Kategori Barang Barang & Jasa", "align": "left", "default": False},
)

DETAIL_COLUMNS = (
    {"key": "nomor", "label": "Nomor #", "align": "left", "default": True},
    {"key": "tanggal", "label": "Tanggal", "align": "left", "default": True},
    {"key": "keterangan", "label": "Keterangan", "align": "left", "default": True},
    {"key": "kuantitas", "label": "Kuantitas (PCS)", "align": "right", "default": True, "numeric": True},
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

CUSTOMER_COLUMNS = (
    {"key": "pelanggan", "label": "Pelanggan", "align": "left", "default": True},
    {"key": "customer_id", "label": "ID Pelanggan", "align": "left", "default": False},
    {"key": "jumlah_faktur", "label": "Jumlah Faktur", "align": "right", "default": True, "numeric": True},
    {"key": "kuantitas", "label": "Kuantitas (PCS)", "align": "right", "default": True, "numeric": True},
    {"key": "penjualan", "label": "Penjualan", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "bpp", "label": "BPP", "align": "right", "default": False, "numeric": True, "money": True},
    {"key": "laba", "label": "Laba", "align": "right", "default": False, "numeric": True, "money": True},
)

SALES_COLUMNS = (
    {"key": "sales", "label": "Sales", "align": "left", "default": True},
    {"key": "sales_user_id", "label": "ID Sales", "align": "left", "default": False},
    {"key": "attribution_status", "label": "Status Atribusi", "align": "left", "default": True},
    {"key": "status_user", "label": "Status User", "align": "left", "default": True},
    {"key": "jumlah_faktur", "label": "Jumlah Faktur", "align": "right", "default": True, "numeric": True},
    {"key": "kuantitas", "label": "Kuantitas (PCS)", "align": "right", "default": True, "numeric": True},
    {"key": "penjualan", "label": "Penjualan", "align": "right", "default": True, "numeric": True, "money": True},
    {"key": "label_mapping", "label": "Label Accurate", "align": "left", "default": False},
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


def _display_number(value: Any) -> Any:
    """Same thousands separator the paper uses, so Excel matches the screen."""
    if value is None or value == "":
        return ""
    text = str(value)
    if not text.replace("-", "", 1).replace(".", "", 1).isdigit():
        return text
    negative = text.startswith("-")
    whole, _, frac = text.replace("-", "", 1).partition(".")
    grouped = f"{int(whole):,}".replace(",", ".")
    shown = f"{grouped},{frac}" if frac else grouped
    return f"-{shown}" if negative else shown


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


def explicit_branch_id(value: Any) -> str:
    """Map one structured id or one exact label. Unknown text stays blank."""
    if isinstance(value, dict):
        for key in ("id", "branch_id", "cabang_id", "_branch_id", "name", "label", "cabang"):
            if key in value and _text(value.get(key)):
                found = explicit_branch_id(value.get(key))
                if found:
                    return found
        return ""
    text = " ".join(_text(value).lower().split())
    if text in {"semua", "all", "[semua cabang]"}:
        return BRANCH_SEMUA
    return _BRANCH_TEXT.get(text, "")


def canonical_branch(*values: Any) -> str:
    for value in values:
        found = explicit_branch_id(value)
        if found and found != BRANCH_SEMUA:
            return found
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
    if BRANCH_SEMUA in tokens or not raw_values:
        return [dict(row) for row in BRANCH_OPTIONS]
    if not specific:
        return []
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
    elif report_id == "penjualan-per-pelanggan":
        source = CUSTOMER_COLUMNS
    elif report_id == "penjualan-per-sales":
        source = SALES_COLUMNS
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
        "penjualan-per-pelanggan": CUSTOMER_COLUMNS,
        "penjualan-per-sales": SALES_COLUMNS,
    }[report_id]
    # Preserve user-selected column ordering without changing totals or amounts.
    requested = None
    if isinstance(layout, dict) and isinstance(layout.get("columns"), list) and layout.get("columns"):
        requested = list(dict.fromkeys(_text(key) for key in layout["columns"]))
    chosen = []
    if requested is None:
        chosen = [dict(col) for col in source if col["default"]]
    else:
        by_key = {col["key"]: col for col in source}
        chosen = [dict(by_key[key]) for key in requested if key in by_key]
    if not chosen:
        chosen = [dict(col) for col in source if col["default"]]
    analysis = _text((layout or {}).get("analysis_row"))
    if report_id == "penjualan-per-barang" and analysis == "nama_kategori_barang":
        chosen = [col for col in chosen if col["key"] not in {"nama_barang", "kode"}]
        if not any(col["key"] == "nama_kategori" for col in chosen):
            chosen.insert(0, dict(next(col for col in SUMMARY_COLUMNS if col["key"] == "nama_kategori")))
        else:
            cat = next(col for col in chosen if col["key"] == "nama_kategori")
            chosen = [cat] + [col for col in chosen if col["key"] != "nama_kategori"]
        if not any(col["key"] == "satuan" for col in chosen):
            satuan = dict(next(col for col in SUMMARY_COLUMNS if col["key"] == "satuan"))
            chosen.insert(1, satuan)
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


def _gross_amount(line: dict) -> Optional[Decimal]:
    if line.get("quantity") in (None, "") or line.get("unit_price") in (None, ""):
        return None
    return _money(_dec(line.get("quantity")) * _dec(line.get("unit_price")))


def _stored_goods_amount(line: dict) -> Optional[Decimal]:
    if line.get("line_subtotal") not in (None, ""):
        return _money(_dec(line.get("line_subtotal")))
    if line.get("line_total") not in (None, ""):
        return _money(_dec(line.get("line_total")))
    return None


def _explicit_line_discount(line: dict) -> Decimal:
    for key in ("line_discount", "line_discount_amount", "discount_amount", "discount"):
        if line.get(key) in (None, ""):
            continue
        amount = _money(_dec(line.get(key)))
        if amount > 0:
            return amount
    return Decimal("0.00")


def _line_discount_outside_stored(line: dict) -> bool:
    """True when the stored subtotal is still gross and a separate line discount exists."""
    gross = _gross_amount(line)
    stored = _stored_goods_amount(line)
    discount = _explicit_line_discount(line)
    if gross is None or stored is None or discount <= 0:
        return False
    return stored == gross


def _net_before_header(line: dict) -> Decimal:
    """Goods value after the line discount, before the invoice header discount."""
    gross = _gross_amount(line)
    stored = _stored_goods_amount(line)
    discount = _explicit_line_discount(line)
    if gross is None:
        return stored if stored is not None else Decimal("0.00")
    if stored is None:
        return _money(max(gross - discount, Decimal("0.00")))
    if stored == _money(gross - discount) or (discount > 0 and stored < gross):
        return stored
    return _money(max(gross - discount, Decimal("0.00")))


def _priced_lines(invoice: dict) -> Tuple[List[dict], List[Decimal], List[Decimal]]:
    """Return lines, header-discount shares, and net penjualan. Shares sum to the header discount."""
    lines = [
        line for line in (invoice.get("lines") or [])
        if isinstance(line, dict) and (_dec(line.get("quantity")) != 0 or (_net_before_header(line) != 0))
    ]
    nets = [_net_before_header(line) for line in lines]
    shares = allocate_header_discount(nets, _dec(invoice.get("discount_amount")))
    penjualan = [_money(net - share) for net, share in zip(nets, shares)]
    outside = any(_line_discount_outside_stored(line) for line in lines)
    stored_after = invoice.get("after_discount")
    if penjualan and stored_after not in (None, "") and not outside:
        drift = _money(_dec(stored_after) - sum(penjualan, Decimal("0.00")))
        if drift != 0 and abs(drift) <= Decimal("0.05"):
            penjualan[-1] = _money(penjualan[-1] + drift)
            shares[-1] = _money(nets[-1] - penjualan[-1])
    return lines, shares, penjualan


def _category_name(line: dict, master: Optional[dict]) -> str:
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
    return NO_CATEGORY


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


def _structured_branch(doc: Optional[dict]) -> Optional[str]:
    """Return a mapped id, '' when a structured id exists but is unknown, or None when absent."""
    if not isinstance(doc, dict):
        return None
    for key in ("branch_id", "cabang_id", "_branch_id"):
        if _text(doc.get(key)):
            found = explicit_branch_id(doc.get(key))
            return "" if found == BRANCH_SEMUA else found
    return None


def _invoice_branch(invoice: dict, order: Optional[dict]) -> str:
    for doc in (invoice, order or {}):
        structured = _structured_branch(doc)
        if structured is not None:
            return structured
    for doc in (invoice, order or {}):
        if not isinstance(doc, dict):
            continue
        for key in ("branch", "cabang", "branch_name", "outlet"):
            found = explicit_branch_id(doc.get(key))
            if found and found != BRANCH_SEMUA:
                return found
    return ""


def _customer_name(invoice: dict, order: Optional[dict]) -> str:
    return _text(
        invoice.get("customer_name")
        or invoice.get("customer")
        or display_customer_name(order)
        or (order or {}).get("customer_name")
        or invoice.get("customer_snapshot_name")
    )


def _stable_customer_id(invoice: dict, order: Optional[dict]) -> Optional[str]:
    """Canonical customer_id from related order (preferred) or invoice. Never name-match."""
    for record in (order, invoice):
        stable = extract_stable_customer_id(record)
        if stable:
            return stable
    return None


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
    unit_settings_by_mid = {}
    for row in await _iter_docs(db, "product_unit_settings"):
        mid = _text(row.get("master_item_id"))
        if mid:
            unit_settings_by_mid[mid] = row
    cogs_by_line: Dict[Tuple[str, str], dict] = {}
    seen_movements: Dict[Tuple[str, str], set] = {}
    for doc in await _iter_docs(db, "inventory_issue_accounting", {"status": "posted"}):
        basis = doc.get("accounting_posting_basis") if isinstance(doc.get("accounting_posting_basis"), dict) else {}
        if basis.get("total_cogs") is None:
            continue
        order_id = _text(basis.get("order_id") or doc.get("order_id"))
        line_id = _text(basis.get("line_id") or doc.get("line_id"))
        line_idx = basis.get("line_idx")
        if line_idx is None:
            line_idx = doc.get("line_idx")
        if not order_id or (not line_id and line_idx in (None, "")):
            continue
        key = (order_id, line_id or f"idx:{line_idx}")
        movement_id = _text(doc.get("movement_id") or basis.get("movement_id"))
        seen = seen_movements.setdefault(key, set())
        if movement_id and movement_id in seen:
            continue
        if movement_id:
            seen.add(movement_id)
        slot = cogs_by_line.setdefault(key, {
            "total": Decimal("0.00"),
            "base_qty": Decimal("0"),
            "base_complete": True,
            "uoms": set(),
        })
        slot["total"] = _money(slot["total"] + _dec(basis.get("total_cogs")))
        raw_base = basis.get("base_qty")
        if raw_base in (None, ""):
            raw_base = basis.get("issue_base_qty")
        if raw_base in (None, ""):
            slot["base_complete"] = False
        else:
            slot["base_qty"] = slot["base_qty"] + _dec(raw_base)
            slot["uoms"].add(_posting_uom(basis, doc))
    return {
        "invoices": invoices,
        "orders": orders,
        "masters_by_code": by_code,
        "masters_by_id": by_id,
        "unit_settings_by_mid": unit_settings_by_mid,
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


def _unit_setting_for(line: dict, master: Optional[dict], ctx: dict) -> Optional[dict]:
    settings = ctx.get("unit_settings_by_mid") or {}
    for key in ("master_item_id", "item_id"):
        token = _text(line.get(key) or (master or {}).get(key) or (master or {}).get("id"))
        if token and token in settings:
            return settings[token]
    return None


def _line_key(invoice: dict, line: dict) -> Tuple[str, str]:
    order_id = _text(invoice.get("po_id") or invoice.get("order_id"))
    line_id = _text(line.get("line_id"))
    if line_id:
        return order_id, line_id
    return order_id, f"idx:{line.get('line_idx')}"


def _normalize_uom(value: Any) -> str:
    return _text(value).casefold()


_PCS_UOM = frozenset({"pcs", "pc", "piece", "pieces"})


def _is_pcs_uom(value: Any) -> bool:
    return _normalize_uom(value) in _PCS_UOM


def _qty_equal(left: Decimal, right: Decimal) -> bool:
    return _dec(left).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP) == _dec(right).quantize(
        Decimal("0.000001"), rounding=ROUND_HALF_UP
    )


def _report_qty_pcs(
    line: dict,
    *,
    master: Optional[dict] = None,
    unit_setting: Optional[dict] = None,
) -> dict:
    """Normalize one invoice line quantity to PCS for reporting only.

    Historical safety: only line-level conversion snapshots are trusted.
    Current ``product_unit_settings`` / master unit are not used to invent PCS
    for older invoices (no effective-dated conversion history on master).

    Does not alter financial amounts or BPP allocation weights.
    """
    del master, unit_setting  # intentionally unused for historical conversion
    tx_qty = _dec(line.get("quantity"))
    # Unit identity must come from the invoice line, not current master.
    tx_uom = _text(line.get("unit"))
    has_base = line.get("base_qty") not in (None, "")
    has_factor = line.get("conversion_factor") not in (None, "")
    base_qty = _dec(line.get("base_qty")) if has_base else None
    base_uom = _text(line.get("base_uom")) if has_base or line.get("base_uom") not in (None, "") else ""
    factor = _dec(line.get("conversion_factor")) if has_factor else None

    def ok(qty: Decimal, source: str) -> dict:
        return {
            "quantity": qty,
            "unit": "Pcs",
            "qty_verified": True,
            "qty_note": "",
            "qty_source": source,
            "tx_quantity": tx_qty,
            "tx_unit": tx_uom,
        }

    def bad(note: str) -> dict:
        label = tx_uom or "—"
        return {
            "quantity": None,
            "unit": f"{label} (belum PCS)",
            "qty_verified": False,
            "qty_note": note,
            "qty_source": None,
            "tx_quantity": tx_qty,
            "tx_unit": tx_uom,
        }

    if tx_qty < 0:
        return bad("kuantitas transaksi negatif tidak dapat dinormalisasi ke PCS")

    if not tx_uom and not has_base and not has_factor:
        return bad("satuan transaksi kosong; konversi ke PCS tidak dapat dibuktikan")

    # 1) Transaction already in PCS.
    if _is_pcs_uom(tx_uom):
        if has_base:
            if not _is_pcs_uom(base_uom or "PCS"):
                return bad("base_uom baris bukan PCS meski satuan transaksi PCS")
            if has_factor:
                if factor is None or factor <= 0:
                    return bad("faktor konversi baris tidak valid")
                if not _qty_equal(tx_qty * factor, base_qty):
                    return bad("base_qty dan conversion_factor tidak konsisten")
                return ok(base_qty, "base_qty")
            if not _qty_equal(base_qty, tx_qty):
                return bad("base_qty PCS tidak sama dengan kuantitas transaksi PCS")
        if has_factor and (factor is None or factor <= 0):
            return bad("faktor konversi baris tidak valid")
        if has_factor and factor != 1 and not has_base:
            # Factor without base while unit is already PCS is ambiguous.
            return bad("conversion_factor pada baris PCS tanpa base_qty tidak dipakai")
        return ok(tx_qty, "tx_pcs")

    # 2) Snapshot base_qty already in PCS — do not multiply again.
    if has_base:
        if base_qty is None or base_qty < 0:
            return bad("base_qty tidak valid")
        if not _is_pcs_uom(base_uom):
            return bad("base_uom snapshot bukan PCS")
        if has_factor:
            if factor is None or factor <= 0:
                return bad("faktor konversi baris tidak valid")
            if not _qty_equal(tx_qty * factor, base_qty):
                return bad("base_qty dan conversion_factor tidak konsisten")
        return ok(base_qty, "base_qty")

    # 3) Snapshot conversion_factor → PCS requires line-level base_uom PCS.
    # Do not corroborate missing base_uom from current master settings.
    if has_factor:
        if factor is None or factor <= 0:
            return bad("faktor konversi baris tidak valid")
        snap_base = _text(line.get("base_uom"))
        if not snap_base:
            return bad(
                "conversion_factor baris tanpa base_uom PCS pada snapshot; "
                "master terkini tidak dipakai untuk histori"
            )
        if not _is_pcs_uom(snap_base):
            return bad("dasar conversion_factor baris bukan PCS")
        return ok(tx_qty * factor, "conversion_factor")

    return bad(
        "konversi ke PCS tidak tersedia pada snapshot baris; "
        "pengaturan produk terkini tidak dipakai untuk transaksi historis"
    )


def _posting_uom(basis: dict, doc: dict) -> str:
    for source in (basis, doc):
        if not isinstance(source, dict):
            continue
        for key in ("base_uom", "transaction_uom"):
            token = _normalize_uom(source.get(key))
            if token:
                return token
    return ""


def _line_weight(line: dict) -> Tuple[Decimal, str]:
    """Quantity of this invoice line in the posting's base unit, when the line itself proves it."""
    qty = _dec(line.get("quantity"))
    if line.get("base_qty") not in (None, ""):
        return _dec(line.get("base_qty")), "base"
    if line.get("conversion_factor") not in (None, ""):
        factor = _dec(line.get("conversion_factor"))
        if factor > 0:
            return qty * factor, "base"
    return qty, "tx"


def _allocation_basis(posting: Optional[dict]) -> Optional[Tuple[Decimal, str]]:
    """Posted quantity and its unit. Missing or mixed units cannot carry a share."""
    if not posting or not posting.get("base_complete"):
        return None
    uoms = posting.get("uoms") or set()
    if len(uoms) != 1:
        return None
    posted_qty = _dec(posting.get("base_qty"))
    if posted_qty <= 0:
        return None
    return posted_qty, next(iter(uoms))


def _claim_weight(row: dict, posted_uom: str) -> Optional[Decimal]:
    mode = row.get("weight_mode")
    if row.get("weight") is None or mode not in {"base", "tx"}:
        return None
    weight = _dec(row.get("weight"))
    if weight < 0:
        return None
    if mode == "base":
        return weight
    # Compare posting UOM to the original transaction unit, never the report PCS label.
    tx_unit = row.get("tx_unit") if row.get("tx_unit") not in (None, "") else row.get("unit")
    if not posted_uom or _normalize_uom(tx_unit) != posted_uom:
        return None
    return weight


def _allocate_posted_cogs(posting: dict, rows: Sequence[dict]) -> List[Optional[Decimal]]:
    """Give each invoice total_cogs × line qty / posted qty.

    The denominator is the posted quantity, not the invoices currently on screen.
    Quantity that is not yet invoiced keeps its cost. A line is left blank when
    its unit cannot be proven or when accepting it would count the same cost twice.
    """
    result: List[Optional[Decimal]] = [None for _ in rows]
    basis = _allocation_basis(posting)
    if basis is None:
        return result
    posted_qty, posted_uom = basis
    total = _money(_dec(posting.get("total")))
    claims: List[Tuple[int, Decimal]] = []
    for index, row in enumerate(rows):
        weight = _claim_weight(row, posted_uom)
        if weight is None:
            continue
        claims.append((index, weight))
    claims.sort(key=lambda item: (
        rows[item[0]].get("invoice_date") or "",
        rows[item[0]].get("invoice_number") or "",
        item[0],
    ))
    claimed = sum((weight for _, weight in claims), Decimal("0"))
    if claimed <= posted_qty:
        accepted = claims
    else:
        accepted = []
        remaining = posted_qty
        for index, weight in claims:
            if weight > remaining:
                continue
            accepted.append((index, weight))
            remaining -= weight
    used = Decimal("0.00")
    for index, weight in accepted:
        share = Decimal("0.00") if weight == 0 else _money(total * weight / posted_qty)
        if used + share > total:
            share = _money(total - used)
        used += share
        result[index] = share
    return result


def _prepared_lines(ctx: dict, start: str, end: str, branch: str) -> Tuple[List[dict], dict]:
    """Return filtered detail lines plus COGS shares computed across all finalized invoices."""
    finalized = []
    for invoice in ctx["invoices"]:
        if effective_financial_status(invoice) != FINANCIAL_FINALIZED:
            continue
        order = ctx["orders"].get(_text(invoice.get("po_id") or invoice.get("order_id")))
        business = _text(invoice.get("invoice_date"))[:10]
        lines, shares, nets = _priced_lines(invoice)
        for index, line in enumerate(lines):
            qty = _dec(line.get("quantity"))
            master = _master_for(line, ctx)
            unit_setting = _unit_setting_for(line, master, ctx)
            weight, weight_mode = _line_weight(line)
            report_qty = _report_qty_pcs(line, master=master, unit_setting=unit_setting)
            finalized.append({
                "invoice_id": invoice.get("id"),
                "invoice_number": _text(invoice.get("invoice_number")),
                "invoice_date": business,
                "customer": _customer_name(invoice, order),
                "customer_id": _stable_customer_id(invoice, order),
                # Invoice Accurate sales label snapshot only — never orders.sales_user_id / CRM owner.
                "sales_label": exact_sales_label(invoice.get("sales") or invoice.get("sales_name")),
                "branch": _invoice_branch(invoice, order),
                "keterangan": _keterangan(line),
                "item_code": _text(line.get("item_code") or (master or {}).get("item_code")),
                "item_name": _text(line.get("item_name") or line.get("name") or (master or {}).get("item_name")) or "—",
                "unit": report_qty["unit"],
                "quantity": report_qty["quantity"],
                "qty_verified": report_qty["qty_verified"],
                "qty_note": report_qty["qty_note"],
                "qty_source": report_qty["qty_source"],
                "tx_quantity": report_qty["tx_quantity"],
                "tx_unit": report_qty["tx_unit"],
                "unit_price": _money(_dec(line.get("unit_price"))),
                "subtotal": _net_before_header(line),
                "discount": shares[index] if index < len(shares) else Decimal("0.00"),
                "penjualan": nets[index],
                "category": _category_name(line, master),
                "line_key": _line_key(invoice, line),
                "grand_total": _money(_dec(invoice.get("grand_total"))),
                "order_id": _text(invoice.get("po_id") or invoice.get("order_id")),
                "weight": weight,
                "weight_mode": weight_mode,
            })
    keys_order: Dict[Tuple[str, str], List[int]] = {}
    for index, row in enumerate(finalized):
        key = row["line_key"]
        if not key[0]:
            continue
        keys_order.setdefault(key, []).append(index)
    for key, indexes in keys_order.items():
        posting = ctx["cogs_by_line"].get(key)
        if not posting:
            continue
        shares = _allocate_posted_cogs(posting, [finalized[index] for index in indexes])
        for index, share in zip(indexes, shares):
            finalized[index]["bpp"] = share
    for row in finalized:
        row.setdefault("bpp", None)
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
        if value is None:
            return None
        return _qty_str(_dec(value))
    return value


def _qty_complete(rows: Sequence[dict]) -> bool:
    return all(row.get("qty_verified", row.get("quantity") is not None) for row in rows)


def _sum_report_qty(rows: Sequence[dict]) -> Optional[Decimal]:
    if not rows:
        return Decimal("0")
    if not _qty_complete(rows):
        return None
    return sum((_dec(row.get("quantity")) for row in rows), Decimal("0"))


def _totals_payload(rows: Sequence[dict], columns: Sequence[dict], *, quantity_by_unit: bool = False) -> dict:
    del quantity_by_unit  # legacy flag; PCS normalization owns quantity totals
    totals = {}
    bpp_complete = all(row.get("bpp") is not None for row in rows) if rows else True
    qty_complete = _qty_complete(rows) if rows else True
    for column in columns:
        key = column["key"]
        if key == "kuantitas":
            total_qty = _sum_report_qty(rows)
            totals[key] = None if total_qty is None else _qty_str(total_qty)
        elif key == "penjualan":
            totals[key] = _money_str(sum((row["penjualan"] for row in rows), Decimal("0.00")))
        elif key == "diskon":
            totals[key] = _money_str(sum((row["discount"] for row in rows), Decimal("0.00")))
        elif key == "total":
            totals[key] = _money_str(sum((_dec(row.get("grand_total")) for row in rows), Decimal("0.00")))
        elif key == "jumlah_faktur":
            totals[key] = int(sum(int(row.get("jumlah_faktur") or 0) for row in rows))
        elif key in {"bpp", "laba"}:
            if not rows or not bpp_complete:
                totals[key] = None
            else:
                totals[key] = _money_str(sum((row[key] for row in rows), Decimal("0.00")))
    totals["bpp_complete"] = bpp_complete
    totals["qty_complete"] = qty_complete
    totals["row_count"] = len(rows)
    return totals


def _summary_rows(lines: Sequence[dict], analysis: str) -> List[dict]:
    buckets: Dict[Tuple, dict] = {}
    order: List[Tuple] = []
    for line in lines:
        if analysis == "nama_kategori_barang":
            # PCS-normalized rows share one unit; unverified keep a distinct bucket.
            if line.get("qty_verified"):
                key = (line.get("category") or "", "Pcs")
            else:
                key = (line.get("category") or "", line.get("unit") or "?", "unverified", line.get("item_code") or "", id(line))
        else:
            if line.get("qty_verified"):
                key = (line["item_code"], line["item_name"], "Pcs")
            else:
                key = (line["item_code"], line["item_name"], line.get("unit") or "?", "unverified", id(line))
        if key not in buckets:
            buckets[key] = {
                "item_code": line["item_code"],
                "item_name": line["item_name"],
                "unit": line["unit"],
                "category": line.get("category"),
                "quantity": Decimal("0") if line.get("qty_verified") else None,
                "qty_verified": True,
                "qty_note": "",
                "penjualan": Decimal("0.00"),
                "discount": Decimal("0.00"),
                "bpp_values": [],
                "lines": [],
            }
            order.append(key)
        bucket = buckets[key]
        if line.get("qty_verified") and bucket["quantity"] is not None:
            bucket["quantity"] += _dec(line.get("quantity"))
        else:
            bucket["quantity"] = None
            bucket["qty_verified"] = False
            bucket["qty_note"] = line.get("qty_note") or bucket["qty_note"] or "kuantitas belum terverifikasi ke PCS"
            if not line.get("qty_verified"):
                bucket["unit"] = line.get("unit") or bucket["unit"]
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
            "unit": bucket["unit"] if bucket["qty_verified"] else bucket["unit"],
            "category": bucket["category"],
            "quantity": bucket["quantity"],
            "qty_verified": bucket["qty_verified"],
            "qty_note": bucket["qty_note"],
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
        sub_qty = _sum_report_qty(members)
        rows.append({
            "kind": "subtotal",
            "label": name,
            "quantity": sub_qty,
            "qty_verified": sub_qty is not None,
            "unit": "Pcs" if sub_qty is not None else "",
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


def _customer_rows(lines: Sequence[dict]) -> List[dict]:
    """Group net sales by stable customer_id. Missing IDs share one bucket only."""
    buckets: Dict[str, dict] = {}
    order: List[str] = []
    for line in lines:
        stable = _text(line.get("customer_id"))
        key = stable or NO_CUSTOMER_ID
        if key not in buckets:
            buckets[key] = {
                "kind": "line",
                "customer_id": stable or None,
                "customer": NO_CUSTOMER_LABEL if key == NO_CUSTOMER_ID else (line.get("customer") or "—"),
                "invoice_ids": set(),
                "penjualan": Decimal("0.00"),
                "quantity": Decimal("0"),
                "qty_verified": True,
                "bpp_values": [],
                "lines": [],
            }
            order.append(key)
        bucket = buckets[key]
        if key != NO_CUSTOMER_ID and line.get("customer"):
            # Prefer a non-empty display name; do not merge by name across IDs.
            if not bucket["customer"] or bucket["customer"] == "—":
                bucket["customer"] = line["customer"]
        inv_token = _text(line.get("invoice_id") or line.get("invoice_number"))
        if inv_token:
            bucket["invoice_ids"].add(inv_token)
        bucket["penjualan"] = _money(bucket["penjualan"] + line["penjualan"])
        if line.get("qty_verified") and bucket["qty_verified"]:
            bucket["quantity"] += _dec(line.get("quantity"))
        else:
            bucket["qty_verified"] = False
            bucket["quantity"] = None
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
            "customer_id": bucket["customer_id"],
            "customer": bucket["customer"],
            "jumlah_faktur": len(bucket["invoice_ids"]),
            "quantity": bucket["quantity"] if bucket["qty_verified"] else None,
            "qty_verified": bucket["qty_verified"],
            "penjualan": bucket["penjualan"],
            "bpp": bpp,
            "laba": laba,
            "drill": None,
        })
    # Stable IDs first (by display name), no-ID bucket last.
    rows.sort(key=lambda row: (
        1 if row.get("customer_id") is None else 0,
        (row.get("customer") or "").casefold(),
        row.get("customer_id") or "",
    ))
    return rows


def _project_customer(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for column in columns:
        key = column["key"]
        if key == "pelanggan":
            raw = row.get("customer") or NO_CUSTOMER_LABEL
        elif key == "customer_id":
            raw = row.get("customer_id") or ""
        elif key == "jumlah_faktur":
            raw = row.get("jumlah_faktur")
        elif key == "kuantitas":
            raw = row.get("quantity")
        elif key == "penjualan":
            raw = row.get("penjualan")
        elif key == "bpp":
            raw = row.get("bpp")
        elif key == "laba":
            raw = row.get("laba")
        else:
            raw = row.get(key)
        values[key] = _cell(column, raw)
    return {"kind": "line", "values": values, "drill": row.get("drill")}


def _user_is_inactive(user: Optional[dict]) -> bool:
    if not user:
        return False
    if user.get("is_active") is False or user.get("active") is False:
        return True
    status = exact_sales_label(user.get("status") or "active").casefold()
    return status in _INACTIVE_USER_STATUSES


def _mapping_index(entries: Optional[Sequence[dict]]) -> Tuple[Dict[str, str], set]:
    """Build exact label→user_id map; labels with >1 entry are ambiguous."""
    mapping: Dict[str, str] = {}
    counts: Dict[str, int] = {}
    ambiguous: set = set()
    for row in entries or []:
        if not isinstance(row, dict):
            continue
        label = exact_sales_label(row.get("label"))
        uid = _text(row.get("user_id"))
        if not label or not uid:
            continue
        counts[label] = counts.get(label, 0) + 1
        if counts[label] > 1:
            ambiguous.add(label)
        mapping[label] = uid
    return mapping, ambiguous


def _resolve_sales_attribution(
    sales_label: Any,
    mapping: Dict[str, str],
    ambiguous: set,
    users_by_id: Dict[str, dict],
) -> dict:
    """MAPPED_CURRENT vs UNVERIFIED from invoice label + current mapping. Read-only."""
    label = exact_sales_label(sales_label)
    if not label:
        return {
            "sales_user_id": None,
            "attribution_status": ATTR_UNVERIFIED,
            "sales_label": "",
            "user_inactive": False,
            "display_name": UNVERIFIED_SALES_LABEL,
        }
    if label in ambiguous:
        return {
            "sales_user_id": None,
            "attribution_status": ATTR_UNVERIFIED,
            "sales_label": label,
            "user_inactive": False,
            "display_name": UNVERIFIED_SALES_LABEL,
        }
    uid = _text(mapping.get(label))
    if not uid:
        return {
            "sales_user_id": None,
            "attribution_status": ATTR_UNVERIFIED,
            "sales_label": label,
            "user_inactive": False,
            "display_name": UNVERIFIED_SALES_LABEL,
        }
    user = users_by_id.get(uid)
    if not user:
        return {
            "sales_user_id": None,
            "attribution_status": ATTR_UNVERIFIED,
            "sales_label": label,
            "user_inactive": False,
            "display_name": UNVERIFIED_SALES_LABEL,
        }
    if exact_sales_label(user.get("role")) != SALES_PWA_ROLE:
        return {
            "sales_user_id": None,
            "attribution_status": ATTR_UNVERIFIED,
            "sales_label": label,
            "user_inactive": False,
            "display_name": UNVERIFIED_SALES_LABEL,
        }
    inactive = _user_is_inactive(user)
    display = _text(user.get("name")) or _text(user.get("email")) or label
    return {
        "sales_user_id": uid,
        "attribution_status": ATTR_MAPPED,
        "sales_label": label,
        "user_inactive": inactive,
        "display_name": display,
    }


def _sales_rows(
    lines: Sequence[dict],
    mapping: Dict[str, str],
    ambiguous: set,
    users_by_id: Dict[str, dict],
) -> List[dict]:
    """Group net sales by current Accurate mapping user_id; unverified share one bucket."""
    buckets: Dict[str, dict] = {}
    order: List[str] = []
    for line in lines:
        attr = _resolve_sales_attribution(line.get("sales_label"), mapping, ambiguous, users_by_id)
        if attr["attribution_status"] == ATTR_MAPPED and attr["sales_user_id"]:
            key = attr["sales_user_id"]
        else:
            key = UNVERIFIED_SALES_ID
        if key not in buckets:
            buckets[key] = {
                "kind": "line",
                "sales_user_id": attr["sales_user_id"] if key != UNVERIFIED_SALES_ID else None,
                "sales": attr["display_name"] if key != UNVERIFIED_SALES_ID else UNVERIFIED_SALES_LABEL,
                "attribution_status": ATTR_MAPPED if key != UNVERIFIED_SALES_ID else ATTR_UNVERIFIED,
                "attribution_label": (
                    "Berdasarkan Mapping Saat Ini" if key != UNVERIFIED_SALES_ID else UNVERIFIED_SALES_LABEL
                ),
                "user_inactive": bool(attr["user_inactive"]) if key != UNVERIFIED_SALES_ID else False,
                "labels": set(),
                "invoice_ids": set(),
                "penjualan": Decimal("0.00"),
                "quantity": Decimal("0"),
                "qty_verified": True,
            }
            order.append(key)
        bucket = buckets[key]
        if key != UNVERIFIED_SALES_ID:
            if attr.get("display_name") and (
                not bucket["sales"] or bucket["sales"] == UNVERIFIED_SALES_LABEL
            ):
                bucket["sales"] = attr["display_name"]
            if attr.get("user_inactive"):
                bucket["user_inactive"] = True
        label = exact_sales_label(attr.get("sales_label") or line.get("sales_label"))
        if label:
            bucket["labels"].add(label)
        inv_token = _text(line.get("invoice_id") or line.get("invoice_number"))
        if inv_token:
            bucket["invoice_ids"].add(inv_token)
        bucket["penjualan"] = _money(bucket["penjualan"] + line["penjualan"])
        if line.get("qty_verified") and bucket["qty_verified"]:
            bucket["quantity"] += _dec(line.get("quantity"))
        else:
            bucket["qty_verified"] = False
            bucket["quantity"] = None
    rows = []
    for key in order:
        bucket = buckets[key]
        rows.append({
            "kind": "line",
            "sales_user_id": bucket["sales_user_id"],
            "sales": bucket["sales"],
            "attribution_status": bucket["attribution_status"],
            "attribution_label": bucket["attribution_label"],
            "user_inactive": bucket["user_inactive"],
            "status_user": (
                "Nonaktif" if bucket["user_inactive"]
                else ("Aktif" if bucket["attribution_status"] == ATTR_MAPPED else "")
            ),
            "label_mapping": ", ".join(sorted(bucket["labels"])),
            "jumlah_faktur": len(bucket["invoice_ids"]),
            "quantity": bucket["quantity"] if bucket["qty_verified"] else None,
            "qty_verified": bucket["qty_verified"],
            "penjualan": bucket["penjualan"],
            "drill": None,
        })
    rows.sort(key=lambda row: (
        1 if row.get("attribution_status") == ATTR_UNVERIFIED else 0,
        (row.get("sales") or "").casefold(),
        row.get("sales_user_id") or "",
    ))
    return rows


def _project_sales(row: dict, columns: Sequence[dict]) -> dict:
    values = {}
    for column in columns:
        key = column["key"]
        if key == "sales":
            raw = row.get("sales") or UNVERIFIED_SALES_LABEL
        elif key == "sales_user_id":
            raw = row.get("sales_user_id") or ""
        elif key == "attribution_status":
            raw = row.get("attribution_label") or row.get("attribution_status") or ""
        elif key == "status_user":
            raw = row.get("status_user") or ""
        elif key == "jumlah_faktur":
            raw = row.get("jumlah_faktur")
        elif key == "kuantitas":
            raw = row.get("quantity")
        elif key == "penjualan":
            raw = row.get("penjualan")
        elif key == "label_mapping":
            raw = row.get("label_mapping") or ""
        else:
            raw = row.get(key)
        values[key] = _cell(column, raw)
    return {"kind": "line", "values": values, "drill": row.get("drill")}


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
        "discount": "diskon header dialokasikan setelah diskon baris; diskon baris tidak dipotong dua kali; sisa 0,01 di baris terakhir",
        "penjualan": "nilai bersih sebelum PPN dan biaya lainnya",
        "invoice_total": "kolom Total pada Daftar Faktur Penjualan adalah grand_total faktur dan boleh berbeda dari Penjualan",
        "tax": "PPN tidak diaktifkan di pekerjaan ini; angka pajak yang sudah tersimpan tidak diubah",
        "bpp": "BPP = total HPP posted × kuantitas baris / kuantitas posting; sisa yang belum difaktur tidak dialokasikan",
        "bpp_missing": "BPP dan laba dikosongkan bila kuantitas posting atau satuan alokasi tidak dapat dibuktikan",
        "category": "kategori master atau baris; kosong ditampilkan sebagai Tanpa Kategori; kuantitas dijumlahkan dalam PCS terverifikasi",
        "quantity_pcs": "kuantitas laporan dinormalisasi ke PCS hanya dari snapshot baris (base_qty/conversion_factor + base_uom PCS); product_unit_settings/master terkini tidak dipakai untuk histori; tidak dijumlahkan bila belum terverifikasi",
        "branch_identity": "branch_id terstruktur didahulukan; teks hanya lewat peta eksplisit",
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
    sales_user_id: Any = "",
    attribution_status: Any = ATTR_ALL,
) -> dict:
    if is_sales_pwa_role(user):
        raise SalesReportError(403, "Laporan ERP tidak tersedia untuk peran ini.")
    spec = next((row for row in _CATALOG if row["id"] == report_id), None)
    if spec is None or not spec["implemented"]:
        raise SalesReportError(404, "Laporan ini tidak ada di daftar Penjualan yang dihitung dari data ERP.")
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
    sales_filter = _text(sales_user_id)
    attr_filter = (_text(attribution_status) or ATTR_ALL).casefold()
    if attr_filter not in {ATTR_ALL, ATTR_MAPPED, ATTR_UNVERIFIED}:
        attr_filter = ATTR_ALL

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
    elif report_id == "penjualan-per-pelanggan":
        full = _customer_rows(lines)
        display_rows = [_project_customer(row, columns) for row in full]
        totals = _totals_payload(full, columns)
        grouped = False
    elif report_id == "penjualan-per-sales":
        mapping_doc = await load_mapping_doc(db)
        mapping, ambiguous = _mapping_index(mapping_doc.get("entries"))
        # Load only mapped user ids — validation for role/active; no mutation.
        user_ids = sorted({uid for uid in mapping.values() if uid})
        users_by_id: Dict[str, dict] = {}
        if user_ids:
            for row in await _iter_docs(db, "users", {"id": {"$in": user_ids}}):
                users_by_id[_text(row.get("id"))] = row
        full = _sales_rows(lines, mapping, ambiguous, users_by_id)
        if attr_filter == ATTR_MAPPED:
            full = [row for row in full if row.get("attribution_status") == ATTR_MAPPED]
        elif attr_filter == ATTR_UNVERIFIED:
            full = [row for row in full if row.get("attribution_status") == ATTR_UNVERIFIED]
        if sales_filter:
            full = [row for row in full if _text(row.get("sales_user_id")) == sales_filter]
        display_rows = [_project_sales(row, columns) for row in full]
        totals = _totals_payload(full, columns)
        meta = {
            **meta,
            "attribution_mode": "mapping_current",
            "mapping_updated_at": mapping_doc.get("updated_at"),
            "ambiguous_labels": sorted(ambiguous),
        }
        grouped = False
    else:
        invoices = _invoice_rows(lines)
        display_rows = [_project_invoice(row, columns) for row in invoices]
        totals = {
            "total": _money_str(sum((row["grand_total"] for row in invoices), Decimal("0.00"))),
            "row_count": len(invoices),
            "bpp_complete": True,
            "qty_complete": True,
        }
        full = invoices
        grouped = False

    if report_id == "rincian-penjualan-per-barang":
        data_count = len(lines)
    elif report_id in {"penjualan-per-barang", "penjualan-per-pelanggan", "penjualan-per-sales"}:
        data_count = len(full)
    else:
        data_count = len(invoices)
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

    gap_parts = []
    if report_id not in {"daftar-faktur-penjualan"} and lines and not totals.get("bpp_complete") and any(
        col["key"] in {"bpp", "laba"} for col in columns
    ):
        gap_parts.append(
            "Sebagian baris belum punya dasar alokasi HPP posted. BPP dan laba baris itu dikosongkan, bukan diisi dari harga beli."
        )
    if report_id not in {"daftar-faktur-penjualan"} and lines and not totals.get("qty_complete") and any(
        col["key"] == "kuantitas" for col in columns
    ):
        gap_parts.append(
            "Sebagian kuantitas belum dapat dinormalisasi ke PCS dari snapshot baris faktur. "
            "Pengaturan satuan produk terkini tidak dipakai untuk mengisi konversi historis. "
            "Nilai itu dikosongkan dan tidak dijumlahkan ke total PCS."
        )
    if report_id == "penjualan-per-pelanggan" and any(row.get("customer_id") is None for row in (full if report_id == "penjualan-per-pelanggan" else [])):
        gap_parts.append(
            "Faktur tanpa customer_id kanonis digabung di kelompok Tanpa ID Pelanggan. "
            "Kesamaan nama tidak dipakai untuk menggabungkan pelanggan."
        )
    gap_detail = None
    if report_id == "penjualan-per-sales":
        gap_parts = [
            "Berdasarkan Mapping Saat Ini (bukan atribusi historis permanen).",
        ]
        if any(row.get("attribution_status") == ATTR_UNVERIFIED for row in full):
            gap_parts.append("Sebagian faktur masuk Belum Terverifikasi.")
        if any(row.get("user_inactive") for row in full):
            gap_parts.append("Sales nonaktif tetap ditampilkan jika mapping dan role valid.")
        gap_detail = (
            "Sumber omzet: sales_invoices finalized; nilai net dan PCS dari engine sales_reports. "
            "Atribusi: exact_sales_label (strip-only) → app_settings.sales_user_mapping → users.id role sales_eternal. "
            "CRM owner dan orders.sales_user_id tidak dipakai. "
            "Label kosong/unmatched/ambiguous/user atau role invalid → Belum Terverifikasi. "
            "Beberapa label Accurate ke satu user_id digabung. "
            "Perubahan mapping mengubah atribusi laporan; data faktur tidak diubah."
        )
    gap = " ".join(gap_parts) if gap_parts else None

    methodology = _methodology(chosen, report_id)
    if report_id == "penjualan-per-sales":
        methodology["sales_attribution"] = (
            "Mapping Sales Accurate saat laporan dijalankan; bukan snapshot ownership historis"
        )
        methodology["sales_identity"] = "exact_sales_label strip-only; no fuzzy; no CRM fallback"

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
        "gap_detail": gap_detail,
        "methodology": methodology,
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
            moneyish = {"penjualan", "bpp", "laba", "diskon", "harga", "total", "kuantitas", "jumlah_faktur"}
            shown = _display_number(raw) if key in moneyish else raw
            cell = sheet.cell(row_index, index, "" if shown is None else shown)
            if key in moneyish and shown not in (None, ""):
                cell.alignment = Alignment(horizontal="right")
        row_index += 1
    totals = report.get("totals") or {}
    if any(key in totals for key in keys):
        sheet.cell(row_index, 1, "Total")
        sheet.cell(row_index, 1).font = Font(bold=True)
        for index, key in enumerate(keys, start=1):
            if key in totals and totals.get(key) is not None:
                moneyish = {"penjualan", "bpp", "laba", "diskon", "harga", "total", "kuantitas", "jumlah_faktur"}
                shown = _display_number(totals.get(key)) if key in moneyish else totals.get(key)
                cell = sheet.cell(row_index, index, shown)
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
