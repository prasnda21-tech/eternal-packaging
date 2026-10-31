/** Shared constants for PO Perlu Produksi visual preview (fixture only). */

export const PO_PERLU_DRILL_PATH =
  "/dashboard/rincian?dashboard=produksi&widget=perlu_produksi";

/** Drill open when user has either of these (audit: OR, not PO Aktif rules). */
export const PO_PERLU_DRILL_ANY_OF = [
  "nav.tasks.kepala_produksi",
  "schedule.edit",
];

export const PO_PERLU_INFO =
  "Jumlah PO unik yang masih memiliki sisa cetak pada baris yang memenuhi aturan. PO HOLD dapat termasuk. Bukan sisa penjadwalan dan bukan jumlah baris item.";

export const PO_PERLU_CAPTION = "PO dengan sisa cetak";
export const PO_PERLU_META = "Snapshot saat ini";
export const PO_PERLU_META_LARGE = "PO unik · Snapshot saat ini";
export const PO_PERLU_ZERO_CAPTION = "Tidak ada PO perlu produksi";

export function formatPoCount(value) {
  return Number(value).toLocaleString("id-ID");
}
