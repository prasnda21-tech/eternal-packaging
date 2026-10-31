/** Dashboard Keuangan catalog. Available widgets have authenticated data sources. */

export const FINANCE_WIDGETS = [
  { id: "active_users", title: "Pengguna Aktif", category: "Aktivitas & Persetujuan", perm: "users.view", endpoint: "GET /dashboard/active-users", summary: "Nama, status Online/Offline, dan interaksi terakhir. Heartbeat web setiap 30 detik; Offline setelah 90 detik tanpa sinyal. Tab yang ditangguhkan browser dapat tampak Offline.", ready: true, period: false, visual: false },
  { id: "cash", title: "Arus Kas", category: "Keuangan & Akuntansi", perm: "dashboard.finance.view", endpoint: "GET /dashboard/keuangan", summary: "Kas masuk, keluar, dan neto pada periode. Transfer antar rekening sendiri tidak masuk arus ini.", ready: true, period: true, visual: false },
  { id: "expense", title: "Beban Perusahaan", category: "Keuangan & Akuntansi", perm: "dashboard.finance.view", endpoint: "GET /dashboard/keuangan", summary: "Komposisi beban operasional. Harga pokok tidak dimasukkan.", ready: true, period: true, visual: true },
  { id: "profit", title: "Laba/Rugi Tahun Ini", category: "Keuangan & Akuntansi", perm: "profit_loss.view", endpoint: "GET /dashboard/widget/laba-rugi", summary: "Komponen laba rugi periode dan tahun berjalan dari jurnal yang sah.", ready: true, period: true, visual: false },
  { id: "purchases", title: "Pembelian", category: "Keuangan & Akuntansi", perm: "purchase_invoices.view", endpoint: "GET /dashboard/pembelian", summary: "Nilai faktur pembelian aktif pada periode. Nilai Faktur Belum Lunas adalah grand_total penuh faktur yang belum LUNAS, termasuk DP. Bukan kas yang dibayarkan dan bukan sisa utang.", ready: true, period: true, visual: false },
  { id: "recon", title: "Rekonsiliasi Bank", category: "Keuangan & Akuntansi", perm: "cash_bank.reconcile", endpoint: "GET /dashboard/widget/reconciliations", summary: "Pencocokan transaksi aktif dan dokumen saldo lama ditampilkan terpisah. Finalized tidak berarti selisih nol.", ready: true, period: false, visual: false },
  { id: "sales", title: "Penjualan", category: "Penjualan & Pelanggan", perm: "dashboard.sales.view", endpoint: "GET /dashboard/penjualan", summary: "Omzet after_discount faktur finalized yang tidak dibatalkan.", ready: true, period: true, visual: false },
  { id: "products", title: "Penjualan Barang", category: "Penjualan & Pelanggan", perm: "dashboard.sales.view", endpoint: "GET /dashboard/penjualan", summary: "Peringkat barang dari line_subtotal. Baris tanpa identitas tidak masuk peringkat.", ready: true, period: true, visual: true },
  { id: "sellers", title: "Performa Penjual", category: "Penjualan & Pelanggan", perm: "dashboard.sales.view", endpoint: "GET /dashboard/penjualan", summary: "Omzet yang terpetakan. Faktur tanpa sales masuk Belum ditetapkan.", ready: true, period: true, visual: true },
  { id: "trend", title: "Tren Penjualan", category: "Penjualan & Pelanggan", perm: "dashboard.sales.view", endpoint: "GET /dashboard/penjualan", summary: "Deret omzet dengan grain yang dihitung service.", ready: true, period: true, visual: true },
];

export const PENDING_WIDGETS = [
  { id: "assets", title: "Aset saat ini", category: "Keuangan & Akuntansi", summary: "Belum ada subtotal aset lancar yang sah." },
  { id: "overdue_invoices", title: "Faktur Terlewat", category: "Keuangan & Akuntansi", summary: "Daftar faktur jatuh tempo masih perlu adapter piutang akuntansi." },
  { id: "cash_available", title: "Ketersediaan Kas", category: "Keuangan & Akuntansi", summary: "Saldo kas hari ini belum terpisah dari arus kas periode." },
  { id: "branch_profit", title: "Laba Cabang", category: "Keuangan & Akuntansi", summary: "Atribusi biaya per cabang belum ada." },
  { id: "customers_owing", title: "Pelanggan Berutang", category: "Keuangan & Akuntansi", summary: "Agregat pelanggan berutang masih perlu kontrak status." },
  { id: "quote_win", title: "Keberhasilan Penawaran", category: "Penjualan & Pelanggan", summary: "Dokumen penawaran belum ada." },
  { id: "quote_stale", title: "Penawaran Terabaikan", category: "Penjualan & Pelanggan", summary: "Dokumen penawaran belum ada." },
  { id: "sales_category", title: "Penjualan Per Kategori", category: "Penjualan & Pelanggan", summary: "Kategori belum menjadi kontrak widget yang lengkap." },
  { id: "sales_brand", title: "Penjualan Per Merek", category: "Penjualan & Pelanggan", summary: "Atribut merek belum ada." },
  { id: "sales_daily", title: "Penjualan per Hari", category: "Penjualan & Pelanggan", summary: "Grain harian hanya tersedia untuk rentang pendek." },
  { id: "customer_growth", title: "Pertumbuhan Pelanggan", category: "Penjualan & Pelanggan", summary: "Belum ada seri pertumbuhan yang sah." },
  { id: "sales_orders", title: "Pesanan Penjualan", category: "Penjualan & Pelanggan", summary: "Hitungan status pesanan bukan pendapatan dan belum dikontrak sebagai widget." },
  { id: "min_stock", title: "Barang Stok Minimum", category: "Operasional & Persediaan", summary: "Ambang minimum pada stok barang belum ada." },
  { id: "hpp", title: "Monitoring HPP", category: "Operasional & Persediaan", summary: "Sumber HPP belum dipilih dan tidak boleh diringkas di kartu." },
  { id: "item_requests", title: "Permintaan Barang", category: "Operasional & Persediaan", summary: "Daftar permintaan belum diagregasi dengan batas baca yang jujur." },
  { id: "purchase_orders", title: "Pesanan Pembelian", category: "Operasional & Persediaan", summary: "Daftar pesanan pembelian belum menjadi widget yang sah." },
  { id: "my_activity", title: "Aktivitas Terakhir Anda", category: "Aktivitas & Persetujuan", summary: "Jejak aktivitas per sesi belum ada." },
  { id: "upcoming", title: "Kegiatan Mendatang", category: "Aktivitas & Persetujuan", summary: "Follow-up mendatang belum dikontrak." },
  { id: "missed", title: "Kegiatan Terlewat", category: "Aktivitas & Persetujuan", summary: "Follow-up terlewat belum dikontrak." },
  { id: "approvals", title: "Persetujuan", category: "Aktivitas & Persetujuan", summary: "Antrean persetujuan masih bercampur dengan tugas produksi." },
  { id: "approvals_done", title: "Persetujuan Terproses", category: "Aktivitas & Persetujuan", summary: "Riwayat persetujuan yang sudah selesai belum ada." },
];

export const READY_IDS = FINANCE_WIDGETS.map((item) => item.id);

export function financeWidgetMeta(id) {
  return FINANCE_WIDGETS.find((item) => item.id === id) || null;
}

export function financeWidgetAllowed(id, hasPerm) {
  const meta = financeWidgetMeta(id);
  if (!meta || typeof hasPerm !== "function") return false;
  return Boolean(hasPerm(meta.perm));
}

export function canOpenFinanceDashboard(hasPerm) {
  return FINANCE_WIDGETS.some((item) => financeWidgetAllowed(item.id, hasPerm));
}

