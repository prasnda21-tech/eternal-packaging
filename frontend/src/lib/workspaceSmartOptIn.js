/**
 * Global Smart Workspace opt-in registry.
 * rememberSearch = restore tab query string across workspace chrome.
 * swr = display-only in-memory list cache (never authorizes writes).
 */

/** Paths that keep ?query when leaving/returning via workspace tabs. */
export const WORKSPACE_REMEMBER_SEARCH_PATHS = new Set([
  // Sales — Phase 0+1 + Phase 2
  "/penjualan/pesanan-penjualan",
  "/penjualan/pengiriman-pesanan",
  "/penjualan/faktur-penjualan",
  "/penjualan/penerimaan-penjualan",
  "/penjualan/import-po",
  // Production / tasks — Phase 3 (URL-ready leaves)
  "/produksi",
  "/produksi/jadwal-pengiriman",
  "/produksi/jadwal-pengiriman-ulang",
  "/tasks/designer",
  "/tasks/kepala-produksi",
  "/tasks/screener",
  "/tasks/helper",
  "/tasks/operator",
  "/tasks/packing",
  "/tasks/surat-jalan",
  "/tasks/loading",
  "/tasks/delivery",
  // Purchasing — Phase 4
  "/pembelian/permintaan",
  "/pembelian/pesanan-pembelian",
  "/pembelian/uang-muka",
  "/pembelian/faktur-pembelian",
  "/pembelian/pembayaran-pembelian",
  "/pembelian/penerimaan-barang",
  "/pembelian/retur",
  // Cash & Bank — Phase 5 (lists only; reconciliation still server-authoritative)
  "/kas-bank/daftar",
  "/kas-bank/penerimaan",
  "/kas-bank/pembayaran",
  "/kas-bank/transfer",
  "/kas-bank/mutasi",
  // Inventory — Phase 6 (master list restore; stock qty never cached as authority)
  "/persediaan/master-barang-jasa",
  "/persediaan/cabang-gudang",
  // Ledger — Phase 7 (journal/account lists; statements remain server-authoritative)
  "/buku-besar/akun-perkiraan",
  "/buku-besar/jurnal-umum",
  "/buku-besar/periode-akuntansi",
  // Reports with list filters
  "/laporan/status-po",
  "/laporan/analitik-output",
  // Employee admin lists — Phase 8 restore only (no sensitive SWR payload)
  "/administrasi-karyawan/pengajuan-saya",
  "/administrasi-karyawan/persetujuan",
  "/administrasi-karyawan/arsip",
]);

/**
 * Paths explicitly excluded from rememberSearch / SWR (forms, live dashboards, sensitive).
 * Documented exceptions for the Phase 0–8 coverage matrix.
 */
export const WORKSPACE_SMART_EXCEPTIONS = Object.freeze({
  "/kas-bank/rekonsiliasi": "reconciliation requires latest server data",
  "/kas-bank/rekonsiliasi-owner": "owner reconciliation requires latest server data",
  "/persediaan/stock-saat-ini": "physical stock must be server-authoritative",
  "/persediaan/nilai-persediaan": "HPP/inventory value must be server-authoritative",
  "/buku-besar/laba-rugi": "financial statement server-authoritative",
  "/buku-besar/neraca": "financial statement server-authoritative",
  "/buku-besar/neraca-saldo": "trial balance server-authoritative",
  "/buku-besar/buku-besar": "ledger inquiry server-authoritative",
  "/buku-besar/piutang-usaha": "AR balances server-authoritative",
  "/buku-besar/hutang-usaha": "AP balances server-authoritative",
  "/buku-besar/saldo-awal": "opening balance form/mutation surface",
  "/pembelian/hpp-pembelian": "HPP calculation server-authoritative",
  "/hr-payroll/daftar-karyawan": "sensitive employee PII — restore-only, no payload cache",
  "/hr-payroll/data-kepegawaian": "sensitive employment data — no payload cache",
  "/hr-payroll/penggajian": "payroll mutation surface",
  "/hr-payroll/slip-gaji": "sensitive payslip data",
  "/hr-payroll/komponen-gaji": "payroll config",
  "/hr-payroll/riwayat": "sensitive payroll history",
  "/hr-payroll/rekonsiliasi": "payroll reconciliation authoritative",
  "/dashboard": "live dashboard",
  "/dashboard/penjualan": "live dashboard",
  "/dashboard/owner": "live dashboard",
  "/chat": "live interface",
  "/spk": "dynamic SPK flows",
  "/settings": "settings forms",
});

export function workspacePathRemembersSearch(path) {
  const clean = String(path || "").split("?")[0].replace(/\/$/, "") || "/";
  return WORKSPACE_REMEMBER_SEARCH_PATHS.has(clean);
}

/** Attach rememberSearch when the path is opted in. */
export function withWorkspaceRememberSearch(tab) {
  if (!tab?.path) return tab;
  if (tab.rememberSearch) return tab;
  if (!workspacePathRemembersSearch(tab.path)) return tab;
  return { ...tab, rememberSearch: true };
}
