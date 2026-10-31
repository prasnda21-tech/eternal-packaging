/** Shared constants for PO Aktif visual preview (fixture only). */

export const PO_AKTIF_DRILL_PATH =
  "/dashboard/rincian?dashboard=produksi&widget=po_aktif&basis=snapshot";

export const PO_AKTIF_INFO =
  "PO unik non-batal yang belum sepenuhnya terkirim. Termasuk HOLD, Urgent, Buffer, dan pengiriman parsial. Selesai produksi belum berarti keluar dari PO Aktif.";

export const PO_AKTIF_CAPTION = "Belum sepenuhnya terkirim";
export const PO_AKTIF_META = "Snapshot saat ini";

export function formatPoCount(value) {
  return Number(value).toLocaleString("id-ID");
}
