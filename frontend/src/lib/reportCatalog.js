/**
 * Report Center catalog — metadata registry only.
 * Visibility is filtered by existing route permissions. No synthetic reports.
 */

export const REPORT_CATEGORY_ORDER = [
  { id: "penjualan", label: "Penjualan" },
  { id: "pembelian", label: "Pembelian" },
  { id: "persediaan", label: "Persediaan" },
  { id: "produksi", label: "Produksi" },
  { id: "keuangan", label: "Keuangan" },
  { id: "piutang_utang", label: "Piutang & Utang" },
  { id: "kas_bank", label: "Kas & Bank" },
  { id: "operasional", label: "Operasional" },
];

const any = (hasPerm, keys) => keys.some((k) => hasPerm(k));

/** Mirrors App.js PermGate anyOf / alsoAnyOf for deep-link targets. */
export const REPORT_CATALOG = Object.freeze([
  {
    id: "penjualan-per-barang",
    category: "penjualan",
    name: "Penjualan per Barang",
    description: "Nilai penjualan per barang dari faktur finalized.",
    to: "/laporan/penjualan?report=penjualan-per-barang",
    tone: "green",
    canAccess: (hp) => hp("reports.sales.view"),
  },
  {
    id: "rincian-penjualan-per-barang",
    category: "penjualan",
    name: "Rincian Penjualan per Barang",
    description: "Rincian baris faktur dengan BPP posted bila tersedia.",
    to: "/laporan/penjualan?report=rincian-penjualan-per-barang",
    tone: "green",
    canAccess: (hp) => hp("reports.sales.view"),
  },
  {
    id: "daftar-faktur-penjualan",
    category: "penjualan",
    name: "Daftar Faktur Penjualan",
    description: "Daftar faktur penjualan finalized per periode.",
    to: "/laporan/penjualan?report=daftar-faktur-penjualan",
    tone: "teal",
    canAccess: (hp) => hp("reports.sales.view"),
  },
  {
    id: "penjualan-per-pelanggan",
    category: "penjualan",
    name: "Penjualan per Pelanggan",
    description: "Nilai penjualan finalized per customer_id kanonis.",
    to: "/laporan/penjualan?report=penjualan-per-pelanggan",
    tone: "green",
    canAccess: (hp) => hp("reports.sales.view"),
  },
  {
    id: "penjualan-per-sales",
    category: "penjualan",
    name: "Penjualan per Sales",
    description: "Nilai penjualan finalized per Mapping Sales Accurate (saat ini).",
    to: "/laporan/penjualan?report=penjualan-per-sales",
    tone: "green",
    canAccess: (hp) => hp("reports.sales.view"),
  },
  {
    id: "daftar-faktur-pembelian",
    category: "pembelian",
    name: "Daftar Faktur Pembelian",
    description: "Daftar faktur pembelian aktif per invoice_date.",
    to: "/laporan/pembelian?report=daftar-faktur-pembelian",
    tone: "amber",
    canAccess: (hp) => hp("reports.purchase.view"),
  },
  {
    id: "pembelian-per-barang",
    category: "pembelian",
    name: "Pembelian per Barang",
    description: "Nilai pembelian bruto per identitas barang dari baris faktur aktif.",
    to: "/laporan/pembelian?report=pembelian-per-barang",
    tone: "amber",
    canAccess: (hp) => hp("reports.purchase.view"),
  },
  {
    id: "pembelian-per-supplier",
    category: "pembelian",
    name: "Pembelian per Supplier",
    description: "Nilai pembelian bruto per nama supplier faktur (supplier_identity_key).",
    to: "/laporan/pembelian?report=pembelian-per-supplier",
    tone: "amber",
    canAccess: (hp) => hp("reports.purchase.view"),
  },
  {
    id: "hpp-pembelian",
    category: "pembelian",
    name: "HPP Pembelian",
    description: "HPP pembelian dari modul pembelian existing.",
    to: "/pembelian/hpp-pembelian",
    tone: "amber",
    canAccess: (hp) => hp("purchase_hpp.view"),
  },
  {
    id: "nilai-persediaan",
    category: "persediaan",
    name: "Nilai Persediaan",
    description: "Valuasi persediaan dan rekonsiliasi terkait.",
    to: "/persediaan/nilai-persediaan",
    tone: "amber",
    canAccess: (hp) => (
      (hp("nav.inventory.inventory_value") && hp("inventory_valuation.view"))
      || hp("inventory_reconciliation.view")
    ),
  },
  {
    id: "kartu-stok",
    category: "persediaan",
    name: "Kartu Stok / Mutasi Barang",
    description: "Mutasi fisik R2 per lokasi dari physical_stock_ledger.",
    to: "/laporan/persediaan?report=kartu-stok",
    tone: "blue",
    canAccess: (hp) => hp("current_stock.view") && hp("master_items.view"),
  },
  {
    id: "stok-saat-ini",
    category: "persediaan",
    name: "Stok Saat Ini",
    description: "Saldo stok fisik per cabang/gudang.",
    to: "/persediaan/stock-saat-ini",
    tone: "blue",
    canAccess: (hp) => hp("nav.inventory.current_stock") && hp("current_stock.view"),
  },
  {
    id: "analitik-output",
    category: "produksi",
    name: "Analitik Output",
    description: "Analitik output operator produksi.",
    to: "/laporan/analitik-output",
    tone: "green",
    canAccess: (hp) => hp("nav.reports.analytics_output") && hp("schedule.view"),
  },
  {
    id: "gaji-operator",
    category: "produksi",
    name: "Gaji Operator",
    description: "Laporan upah operator berdasarkan output.",
    to: "/laporan/gaji-operator",
    tone: "purple",
    canAccess: (hp) => (
      hp("nav.reports.gaji_operator")
      && (hp("reports.gaji_operator.view") || hp("schedule.view"))
    ),
  },
  {
    id: "penggunaan-cat",
    category: "produksi",
    name: "Laporan Penggunaan Cat",
    description: "Laporan penggunaan cat di Sentralisasi Cat.",
    to: "/produksi/sentralisasi-cat",
    tone: "teal",
    canAccess: (hp) => hp("paint.report.view"),
  },
  {
    id: "laba-rugi",
    category: "keuangan",
    name: "Laba Rugi",
    description: "Laba/rugi dari jurnal posted per periode.",
    to: "/buku-besar/laba-rugi",
    tone: "emerald",
    canAccess: (hp) => any(hp, ["nav.ledger.profit_loss", "nav.ledger", "profit_loss.view"]),
  },
  {
    id: "neraca",
    category: "keuangan",
    name: "Neraca",
    description: "Posisi keuangan as-of dari jurnal posted.",
    to: "/buku-besar/neraca",
    tone: "emerald",
    canAccess: (hp) => any(hp, ["nav.ledger.balance_sheet", "nav.ledger", "balance_sheet.view"]),
  },
  {
    id: "neraca-saldo",
    category: "keuangan",
    name: "Neraca Saldo",
    description: "Neraca saldo akun dari jurnal posted.",
    to: "/buku-besar/neraca-saldo",
    tone: "blue",
    canAccess: (hp) => any(hp, ["nav.ledger.trial_balance", "nav.ledger", "trial_balance.view"]),
  },
  {
    id: "buku-besar",
    category: "keuangan",
    name: "Buku Besar",
    description: "Buku besar per akun dari jurnal posted.",
    to: "/buku-besar/buku-besar",
    tone: "blue",
    canAccess: (hp) => any(hp, ["nav.ledger.general_ledger", "nav.ledger", "general_ledger.view"]),
  },
  {
    id: "piutang-usaha",
    category: "piutang_utang",
    name: "Piutang Usaha",
    description: "Piutang usaha dari jurnal dan faktur terkait.",
    to: "/buku-besar/piutang-usaha",
    tone: "amber",
    canAccess: (hp) => any(hp, ["nav.ledger.accounts_receivable", "accounts_receivable.view"]),
  },
  {
    id: "hutang-usaha",
    category: "piutang_utang",
    name: "Hutang Usaha",
    description: "Hutang usaha dari jurnal pembelian terkait.",
    to: "/buku-besar/hutang-usaha",
    tone: "orange",
    canAccess: (hp) => any(hp, ["nav.ledger.accounts_payable", "accounts_payable.view"]),
  },
  {
    id: "mutasi-kas-bank",
    category: "kas_bank",
    name: "Mutasi Kas & Bank",
    description: "Mutasi kas dan bank dari modul Kas & Bank.",
    to: "/kas-bank/mutasi",
    tone: "cyan",
    canAccess: (hp) => any(hp, ["nav.cash_bank.mutasi", "nav.cash_bank", "cash_bank.view"]),
  },
]);

const FAV_PREFIX = "eternal-report-favorites-v1:";

export function reportFavoritesStorageKey(userId) {
  return FAV_PREFIX + String(userId || "guest");
}

export function readReportFavorites(userId) {
  try {
    const raw = window.localStorage.getItem(reportFavoritesStorageKey(userId));
    const parsed = JSON.parse(raw || "[]");
    if (!Array.isArray(parsed)) return [];
    return [...new Set(parsed
      .filter((id) => typeof id === "string" || typeof id === "number")
      .map((id) => String(id).trim())
      .filter(Boolean))];
  } catch {
    return [];
  }
}

export function writeReportFavorites(userId, ids) {
  try {
    const clean = [...new Set((ids || [])
      .filter((id) => typeof id === "string" || typeof id === "number")
      .map((id) => String(id).trim())
      .filter(Boolean))];
    window.localStorage.setItem(reportFavoritesStorageKey(userId), JSON.stringify(clean));
    return clean;
  } catch {
    return [];
  }
}

export function toggleReportFavorite(userId, reportId) {
  const id = String(reportId || "").trim();
  if (!id) return readReportFavorites(userId);
  const current = readReportFavorites(userId);
  const next = current.includes(id) ? current.filter((x) => x !== id) : [...current, id];
  return writeReportFavorites(userId, next);
}

export function visibleReportCatalog(hasPerm) {
  if (typeof hasPerm !== "function") return [];
  return REPORT_CATALOG.filter((row) => {
    try {
      return !!row.canAccess(hasPerm);
    } catch {
      return false;
    }
  });
}

export function userHasReportCatalogAccess(hasPerm) {
  return visibleReportCatalog(hasPerm).length > 0;
}

export function filterReportCatalog(rows, { query = "", category = "all", favoriteIds = null } = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const q = String(query || "").trim().toLocaleLowerCase("id-ID");
  const fav = favoriteIds == null ? null : new Set(favoriteIds);
  return list.filter((row) => {
    if (category === "favorites") {
      if (!fav || !fav.has(row.id)) return false;
    } else if (category && category !== "all" && row.category !== category) {
      return false;
    }
    if (!q) return true;
    const hay = `${row.name} ${row.description} ${row.category}`.toLocaleLowerCase("id-ID");
    return hay.includes(q);
  });
}

export function categoryCounts(rows) {
  const counts = { all: rows.length };
  REPORT_CATEGORY_ORDER.forEach((c) => { counts[c.id] = 0; });
  rows.forEach((row) => {
    counts[row.category] = (counts[row.category] || 0) + 1;
  });
  return counts;
}
