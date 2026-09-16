/**
 * Isolated Status PO matrix preview rows.
 * localhost:3003 or ?previewStatusPoMatrix=1, development only. Never production DB.
 */

export const STATUS_PO_MATRIX_PREVIEW_PARAM = "previewStatusPoMatrix";

export function isStatusPoMatrixPreviewEnabled(search, win = (typeof window !== "undefined" ? window : undefined)) {
  if (process.env.NODE_ENV !== "development") return false;
  const raw = String(search || (win?.location?.search || ""));
  const normalized = raw.startsWith("?") ? raw.slice(1) : raw;
  const params = new URLSearchParams(normalized);
  if (params.get(STATUS_PO_MATRIX_PREVIEW_PARAM) === "1") return true;
  if (!win || !win.location) return false;
  const host = String(win.location.hostname || "");
  const localPreviewHost = host === "localhost" || host === "127.0.0.1";
  return localPreviewHost && String(win.location.port) === "3003";
}

export function buildStatusPoMatrixPreviewRows() {
  return [
    { id: "spo-pack", po_number: "PO-PACK", order_date: "2026-09-01", customer: "PT Packing Aktif", status: "Packing", percent_delivered: 0, current_stage: 11, cancelled: false, hold: false },
    { id: "spo-cetak", po_number: "PO-CETAK", order_date: "2026-09-02", customer: "CV Cetak Jaya", status: "Cetak berjalan", percent_delivered: 0, current_stage: 10, cancelled: false, hold: false },
    { id: "spo-load", po_number: "PO-LOAD", order_date: "2026-09-03", customer: "UD Loading Prima", status: "Loading", percent_delivered: 40, current_stage: 13, cancelled: false, hold: false },
    { id: "spo-jadwal", po_number: "PO-JADWAL", order_date: "2026-09-04", customer: "PT Jadwal Nusantara", status: "Penjadwalan", percent_delivered: 0, current_stage: 5, cancelled: false, hold: false },
    { id: "spo-design", po_number: "PO-DESIGN", order_date: "2026-09-05", customer: "Studio Design One", status: "Design", percent_delivered: 0, current_stage: 1, cancelled: false, hold: false },
    { id: "spo-pay", po_number: "PO-PAY", order_date: "2026-09-06", customer: "PT Menunggu Payment", status: "Payment", percent_delivered: 0, current_stage: 3, cancelled: false, hold: false },
    { id: "spo-screen", po_number: "PO-SCREEN", order_date: "2026-09-07", customer: "Screen House", status: "Screener", percent_delivered: 0, current_stage: 6, cancelled: false, hold: false },
    { id: "spo-sample", po_number: "PO-SAMPLE", order_date: "2026-09-08", customer: "Sample Lab", status: "Konfirmasi Sample", percent_delivered: 0, current_stage: 9, cancelled: false, hold: false },
    { id: "spo-cancel", po_number: "PO-CANCEL", order_date: "2026-08-20", customer: "PO Dibatalkan", status: "Cancelled", percent_delivered: 0, current_stage: 4, cancelled: true, hold: false },
    { id: "spo-kirim", po_number: "PO-KIRIM", order_date: "2026-08-10", customer: "Terkirim Lengkap", status: "Terkirim", percent_delivered: 100, current_stage: 14, cancelled: false, hold: false },
  ];
}
