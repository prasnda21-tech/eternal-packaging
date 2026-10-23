/** Production widget catalog. Snapshot widgets plus Wave 1 analytics that have a real source. */

export const PRODUCTION_PERM = "dashboard.production.view";
export const OPERATOR_ANALYTICS_PERM = "dashboard.production.operator_analytics";

export const PRODUCTION_WIDGETS = [
  { id: "po_aktif", title: "PO Aktif", category: "Snapshot", perm: PRODUCTION_PERM, summary: "PO unik yang belum sepenuhnya terkirim. Snapshot saat ini, bukan histori.", ready: true },
  { id: "perlu_produksi", title: "PO Perlu Produksi", category: "Snapshot", perm: PRODUCTION_PERM, summary: "PO unik dengan sisa qty cetak pada line yang sah. Bukan sisa penjadwalan.", ready: true },
  { id: "sisa_qty", title: "Sisa Qty Produksi", category: "Snapshot", perm: PRODUCTION_PERM, summary: "Sisa qty cetak per satuan. Satuan berbeda tidak digabung.", ready: true },
  { id: "po_urgent", title: "PO Urgent", category: "Snapshot", perm: PRODUCTION_PERM, summary: "PO aktif dengan prioritas workflow urgent. Toggle urgent lama tidak dihitung.", ready: true },
  { id: "po_hold", title: "PO HOLD", category: "Snapshot", perm: PRODUCTION_PERM, summary: "PO unik pada snapshot HOLD. Beberapa line tidak menggandakan PO.", ready: true },
  { id: "deadline", title: "Deadline Pengiriman", category: "Snapshot", perm: PRODUCTION_PERM, summary: "Jendela ≤ 6 jam dan ≤ 24 jam. Bukan H-1 kalender, dan tidak dijumlahkan.", ready: true },
  { id: "approval", title: "Menunggu Approval", category: "Monitoring", perm: PRODUCTION_PERM, summary: "Design dan Payment terpisah. Satu PO boleh menunggu keduanya.", ready: true },
  { id: "prioritas", title: "Prioritas Pekerjaan", category: "Monitoring", perm: PRODUCTION_PERM, summary: "Antrean Kepala Produksi dengan identitas PO dan line yang stabil.", ready: true },
  { id: "output_hari_ini", title: "Output Cetak Hari Ini", category: "Analitik", perm: PRODUCTION_PERM, summary: "Qty cetak yang dikonfirmasi selesai hari ini (WIB), per satuan. Entri legacy tanpa tanggal asli tidak dihitung.", ready: true },
  { id: "tren_output", title: "Tren Output Produksi", category: "Analitik", perm: PRODUCTION_PERM, summary: "Output harian terkonfirmasi, 7 atau 30 hari. Satu seri untuk satu satuan.", ready: true },
  { id: "distribusi_tahap", title: "Distribusi Tahap Workflow", category: "Monitoring", perm: PRODUCTION_PERM, summary: "Satu PO dihitung sekali pada tahap paling tertinggal. Mode per line belum tersedia.", ready: true },
  { id: "output_operator", title: "Performa Operator", category: "Analitik", perm: OPERATOR_ANALYTICS_PERM, summary: "Output aktual per operator. Target Awal dan Target Revisi hanya muncul setelah jadwal difinalisasi.", ready: true },
  { id: "beban_operator", title: "Beban Kerja Operator", category: "Analitik", perm: OPERATOR_ANALYTICS_PERM, summary: "Sisa alokasi aktif per satuan, plus jumlah PO dan line unik.", ready: true },
  { id: "kesiapan", title: "Kesiapan Produksi & Pengiriman", category: "Monitoring", perm: PRODUCTION_PERM, summary: "Siap Produksi berarti Design dan Payment sah. Hasil Cetak Tersedia berarti qty cetak terkonfirmasi. HOLD dan cetak selesai bukan izin jadwal atau kirim.", ready: true },
  { id: "target_produksi", title: "Pencapaian Target Produksi", category: "Analitik", perm: PRODUCTION_PERM, summary: "Target Awal dari finalisasi jadwal. Realisasi cohort mengikuti tanggal jadwal, bukan hari konfirmasi.", ready: true },
  { id: "po_terlambat", title: "PO Terlambat", category: "Snapshot", perm: PRODUCTION_PERM, summary: "Lewat tanggal kirim efektif dan qty wajib belum terkirim penuh. Buffer Stock tidak dihitung terlambat.", ready: true },
  { id: "target_realisasi", title: "Target vs Realisasi", category: "Analitik", perm: PRODUCTION_PERM, summary: "Target Awal, Target Revisi, dan realisasi cohort. Output kalender tetap di tren terpisah.", ready: true },
  { id: "aging", title: "Aging Pekerjaan", category: "Monitoring", perm: PRODUCTION_PERM, summary: "Aging produksi berakhir saat packing selesai. Aging total PO berakhir saat qty wajib terkirim.", ready: true },
  { id: "ketepatan", title: "Ketepatan Penyelesaian", category: "Analitik", perm: PRODUCTION_PERM, summary: "Ketepatan produksi memakai target selesai PO. Ketepatan pengiriman memakai tanggal kirim efektif.", ready: true },
  { id: "bottleneck", title: "Bottleneck Workflow", category: "Monitoring", perm: PRODUCTION_PERM, summary: "PO dan line yang melewati target selesai produksi. HOLD dan approval tertunda bukan bottleneck produksi.", ready: true },
];

export const PENDING_WIDGETS = [
  { id: "komposisi", title: "Komposisi Produk Diproduksi", category: "Analitik", summary: "Pengelompokan nama barang belum memakai identitas line." },
  { id: "qc_reject", title: "QC, Reject & Cetak Ulang", category: "Analitik", summary: "Belum ada pencatatan QC, reject, atau cetak ulang yang sah." },
  { id: "utilisasi_mesin", title: "Utilisasi Mesin", category: "Analitik", summary: "Jam kerja mesin tidak tersedia dan tidak boleh diasumsikan." },
  { id: "output_cabang", title: "Output per Cabang", category: "Analitik", summary: "Filter cabang otoritatif belum ada pada sumber produksi." },
];

export const READY_IDS = PRODUCTION_WIDGETS.map((item) => item.id);

export function productionWidgetMeta(id) {
  return PRODUCTION_WIDGETS.find((item) => item.id === id) || null;
}

export function productionWidgetAllowed(id, hasPerm) {
  const meta = productionWidgetMeta(id);
  if (!meta || typeof hasPerm !== "function") return false;
  return Boolean(hasPerm(meta.perm));
}
