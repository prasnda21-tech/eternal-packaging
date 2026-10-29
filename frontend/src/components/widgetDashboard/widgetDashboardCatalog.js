/** Widget catalog shared by the integrated dashboard. No sample amounts. */

/** GET /delivery/schedule accepts any of these, and role admin before the check. */
export const DELIVERY_SCHEDULE_PERMS = [
  "delivery.schedule.view",
  "schedule.view",
  "nav.delivery_schedule",
];

export const TABS = [
  {
    id: "penjualan",
    label: "Penjualan",
    panels: [
      { id: "ringkasan", label: "Ringkasan", layout: "layout-a", widgets: ["omzet", "settlement", "trend", "sales", "products", "aging"] },
      { id: "pelanggan", label: "Pelanggan & Hari ini", layout: "layout-b", widgets: ["customers", "today"] },
    ],
  },
  {
    id: "operasi",
    label: "Operasi",
    panels: [
      { id: "jadwal", label: "Jadwal", layout: "layout-c", widgets: ["shipToday", "shipLate", "shipNext", "production"] },
    ],
  },
  {
    id: "keuangan",
    label: "Keuangan",
    panels: [
      { id: "ringkasan", label: "Ringkasan", layout: "layout-d", widgets: ["purchases", "ap", "cash", "expense", "pl", "assets"] },
      { id: "bank", label: "Piutang & Bank", layout: "layout-e", widgets: ["overdue", "recon"] },
    ],
  },
];

export const WIDGETS = {
  omzet: {
    code: "S1",
    title: "Omzet periode",
    kicker: "Nilai barang setelah diskon",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · after_discount",
    info: "Nilai barang pada faktur finalized yang tidak dibatalkan, setelah diskon barang, sebelum pajak dan biaya lain. Kartu pelunasan memakai total tagihan. Bukan sisa piutang. Cakupan sales_eternal mengikuti pesanan miliknya.",
  },
  settlement: {
    code: "S2",
    title: "Pelunasan faktur periode",
    kicker: "Total tagihan, status saat ini",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · settlement · grand_total",
    info: "Dua batang adalah grand_total faktur periode menurut status pelunasan saat ini. Lunas adalah nilai penuh faktur yang sudah tertutup. Belum lunas adalah nilai penuh faktur yang masih terbuka, termasuk yang baru dibayar sebagian. Sisa tagihan tidak dipakai sebagai nilai kelompok. Kedua batang memakai jumlah grand_total yang sama. Bukan omzet dan bukan kas pada tanggal faktur.",
  },
  trend: {
    code: "S3",
    title: "Tren omzet",
    kicker: "Grain dan pembanding yang sah",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · trend",
    info: "Titik mengikuti grain_for pada rentang yang dipilih. Pembanding hanya ditampilkan bila service menandainya tersedia. Dasarnya sama dengan omzet.",
  },
  sales: {
    code: "S4",
    title: "Performa sales",
    kicker: "Nominal dan kontribusi",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · sales",
    info: "Nominal omzet yang terpetakan ke pengguna. Kontribusi persen hanya ditampilkan bila service menandainya lengkap dan total omzet lebih dari nol. Tidak ada target tersimpan. Faktur tanpa pemetaan masuk Belum ditetapkan dan membuat data tidak lengkap.",
  },
  products: {
    code: "S5",
    title: "Lima barang teratas",
    kicker: "Nilai baris sebelum diskon faktur",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · products.all_items · line_subtotal",
    info: "Lima baris pertama dari agregasi line_subtotal. Persen memakai seluruh grup yang sah sebelum batas lima. Lihat semua memakai daftar yang sama. Baris tanpa identitas tidak masuk peringkat dan tetap memunculkan peringatan.",
  },
  aging: {
    code: "S6",
    title: "Umur piutang operasional saat ini",
    kicker: "Sisa tagihan sekarang, bukan akhir filter",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · receivables",
    info: "Sisa faktur operasional pada saat permintaan, termasuk yang tidak masuk bucket. Bukan saldo akhir filter dan bukan piutang akuntansi.",
  },
  customers: {
    code: "S7",
    title: "Ranking pelanggan",
    kicker: "Omzet per pelanggan yang identitasnya stabil",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · customer_rank",
    info: "after_discount faktur yang sama dengan omzet, dikelompokkan menurut customer_id yang stabil. Nama yang sama tidak digabung. Faktur tanpa identitas tidak diranking. Jumlah faktur dihitung dari himpunan yang sama. Istilah customer_id hanya ada di catatan ini.",
  },
  today: {
    code: "S8",
    title: "Ringkasan hari ini",
    kicker: "Hari ini, Asia/Jakarta",
    perm: "dashboard.sales.view",
    endpoint: "GET /dashboard/penjualan · preset today",
    info: "Total omzet, jumlah faktur, dan rata-rata untuk hari ini zona Asia/Jakarta. Rata-rata hanya bila status lengkap dan jumlah faktur lebih dari nol. Jenis barang, kuantitas, dan daftar faktur tidak ditampilkan.",
  },
  shipToday: {
    code: "O1",
    title: "Pengiriman hari ini",
    kicker: "Status hari ini",
    perm: "po.view",
    endpoint: "GET /dashboard/today · shipments_today status hari_ini",
    info: "Baris yang service tandai hari_ini. Bukan hitungan surat jalan. Bila daftar pratinjau terpotong, jumlah memakai total service dan tabel ditandai belum lengkap.",
  },
  shipLate: {
    code: "O2",
    title: "Pengiriman terlambat",
    kicker: "Status terlambat",
    perm: "delivery.schedule.view",
    perms: DELIVERY_SCHEDULE_PERMS,
    endpoint: "GET /delivery/schedule · status terlambat",
    info: "Mengikuti status service. Stok buffer tidak pernah terlambat. Bukan tanggal pesanan dibuat. Endpoint menerima role admin, atau delivery.schedule.view, schedule.view, atau nav.delivery_schedule.",
  },
  shipNext: {
    code: "O3",
    title: "Pengiriman mendatang",
    kicker: "Tanggal kirim efektif sesudah hari ini",
    perm: "delivery.schedule.view",
    perms: DELIVERY_SCHEDULE_PERMS,
    endpoint: "GET /delivery/schedule · tanggal pengiriman efektif",
    info: "Tanggal pengiriman efektif sesudah hari ini. Bukan created_at dan bukan hitungan surat jalan. Endpoint menerima role admin, atau delivery.schedule.view, schedule.view, atau nav.delivery_schedule.",
  },
  production: {
    code: "O4",
    title: "Pengerjaan mendatang",
    kicker: "Tanggal pengerjaan",
    perm: "schedule.view",
    endpoint: "GET /dashboard/production · work_date",
    info: "work_date pada pesanan atau operator yang jatuh sesudah hari ini. Bukan target selesai dan bukan jadwal pengiriman.",
  },
  purchases: {
    code: "F1",
    title: "Pembelian periode",
    kicker: "Nilai faktur, bukan kas pembayaran",
    perm: "purchase_invoices.view",
    endpoint: "GET /dashboard/pembelian",
    info: "Jumlah grand_total faktur pembelian aktif menurut invoice_date. Lunas hanya bila pembayaran PAID yang tidak dibatalkan menutup grand_total. Nilai Faktur Belum Lunas adalah grand_total penuh faktur yang belum berstatus LUNAS, termasuk yang sudah DP. Bukan sisa utang. Izin purchase_invoices.view, bukan dashboard.finance.view.",
  },
  ap: {
    code: "F2",
    title: "Utang akuntansi saat ini",
    kicker: "Saldo jurnal sekarang",
    perm: "accounts_payable.view",
    endpoint: "GET /ledger/accounts-payable/summary",
    info: "Saldo utang akuntansi saat ini. Bukan sisa faktur pembelian dan bukan saldo historis di akhir filter. Filter periode tidak mengubah kartu ini.",
  },
  cash: {
    code: "F3",
    title: "Arus kas periode",
    kicker: "Masuk, keluar, dan neto",
    perm: "dashboard.finance.view",
    endpoint: "GET /dashboard/keuangan · cash_movement",
    info: "Kas masuk, keluar, dan neto. Transfer internal tidak masuk arus ini. Bukan laporan arus kas formal dan bukan saldo kas.",
  },
  expense: {
    code: "F4",
    title: "Beban per akun",
    kicker: "Akun beban, tanpa harga pokok",
    perm: "dashboard.finance.view",
    endpoint: "GET /dashboard/keuangan · expense_composition",
    info: "Komposisi beban dari laba rugi. Harga pokok tidak dimasukkan.",
  },
  pl: {
    code: "F5",
    title: "Laba/rugi",
    kicker: "Periode dan tahun berjalan",
    perm: "profit_loss.view",
    endpoint: "GET /dashboard/widget/laba-rugi",
    info: "Endpoint widget ini memakai izin profit_loss.view. Dashboard keuangan lama memakai dashboard.finance.view dan tidak membuka kartu ini. Komponen dan status tidak lengkap mengikuti service laba rugi. Tahun berjalan adalah 1 Januari sampai hari ini zona Asia/Jakarta.",
  },
  assets: {
    code: "F6",
    title: "Total aset",
    kicker: "Total aset neraca hari ini",
    perm: "balance_sheet.view",
    endpoint: "GET /ledger/balance-sheet · summary.total_assets",
    info: "Angka kartu adalah summary.total_assets pada hari ini. Selisih neraca ditampilkan bila ada. Bukan jumlah kartu lain. Filter periode tidak mengubah saldo ini. Nama field summary.total_assets hanya ada di catatan ini.",
  },
  overdue: {
    code: "F7",
    title: "Pelanggan menunggak",
    kicker: "Piutang akuntansi yang sudah lewat",
    perm: "accounts_receivable.view",
    endpoint: "GET /ledger/accounts-receivable/overdue-customers",
    info: "Saldo positif yang lewat jatuh tempo, dijumlah menurut customer_key. Lama adalah days_overdue terbesar. Kunci ambigu tetap terpisah. Bukan sisa operasional. Izin accounts_receivable.view. Filter periode tidak mengubah saldo ini.",
  },
  recon: {
    code: "F8",
    title: "Dokumen rekonsiliasi bank",
    kicker: "Akun, tanggal, status, dan selisih",
    perm: "cash_bank.reconcile",
    endpoint: "GET /dashboard/widget/reconciliations",
    info: "Dokumen rekonsiliasi yang ada. Akun mengikuti syarat BANK pada service, bukan nama akun. Finalized tidak berarti selisih nol. Daftar kosong berarti belum ada dokumen. Izin cash_bank.reconcile.",
  },
};

export const WIDGET_IDS = TABS.flatMap((tab) => tab.panels.flatMap((panel) => panel.widgets));

/** Same contract as GET /delivery/schedule. Admin is not a bypass for other widgets. */
export function canReadDeliverySchedule(user, hasPerm) {
  if (user?.role === "admin") return true;
  if (typeof hasPerm !== "function") return false;
  return DELIVERY_SCHEDULE_PERMS.some((key) => hasPerm(key));
}

export function widgetAllowed(id, user, hasPerm) {
  if (id === "shipLate" || id === "shipNext") return canReadDeliverySchedule(user, hasPerm);
  const meta = WIDGETS[id];
  if (!meta || typeof hasPerm !== "function") return false;
  return (meta.perms || [meta.perm]).some((key) => hasPerm(key));
}

export function canOpenWidgetDashboard(user, hasPerm) {
  return WIDGET_IDS.some((id) => widgetAllowed(id, user, hasPerm));
}
