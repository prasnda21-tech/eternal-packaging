/**
 * Isolated Status PO preview rows via ?previewStatusPoMatrix=1, development only.
 */

export const STATUS_PO_MATRIX_PREVIEW_PARAM = "previewStatusPoMatrix";

const STAGE_KEYS = [
  "order", "design", "appr_design", "payment", "appr_payment",
  "jadwal", "screen", "helper", "operator", "sample",
  "cetak", "packing", "sj", "loading", "kirim",
];

function wf(overrides = {}) {
  const base = Object.fromEntries(STAGE_KEYS.map((k) => [k, "pending"]));
  base.order = "completed";
  return { ...base, ...overrides };
}

export function isStatusPoMatrixPreviewEnabled(search, win = (typeof window !== "undefined" ? window : undefined)) {
  if (process.env.NODE_ENV !== "development") return false;
  const raw = String(search || (win?.location?.search || ""));
  const normalized = raw.startsWith("?") ? raw.slice(1) : raw;
  const params = new URLSearchParams(normalized);
  if (params.get(STATUS_PO_MATRIX_PREVIEW_PARAM) === "1") return true;
  return false;
}

export function buildStatusPoMatrixPreviewRows() {
  return [
    {
      id: "spo-pack",
      po_number: "PO-PACK",
      order_date: "2026-09-01",
      customer: "PT Packing Aktif",
      item_lines: ["Cup 16oz", "Lid 98 mm", "Add On Warna"],
      status: "Packing",
      percent_delivered: 0,
      current_stage: 11,
      cancelled: false,
      hold: false,
      workflow: wf({
        design: "completed", appr_design: "completed", payment: "completed", appr_payment: "completed",
        jadwal: "completed", screen: "completed", helper: "completed", operator: "completed", sample: "completed",
        cetak: "completed", packing: "active",
      }),
    },
    {
      id: "spo-cetak",
      po_number: "PO-CETAK",
      order_date: "2026-09-02",
      customer: "CV Cetak Jaya",
      item_lines: ["Karton Offset"],
      status: "Cetak berjalan",
      percent_delivered: 0,
      current_stage: 10,
      cancelled: false,
      hold: false,
      workflow: wf({
        design: "completed", appr_design: "completed", payment: "completed", appr_payment: "completed",
        jadwal: "completed", screen: "completed", helper: "completed", operator: "completed", sample: "completed",
        cetak: "active",
      }),
    },
    {
      id: "spo-load",
      po_number: "PO-LOAD",
      order_date: "2026-09-03",
      customer: "UD Loading Prima",
      item_lines: ["Box Delivery"],
      status: "Loading",
      percent_delivered: 40,
      current_stage: 13,
      cancelled: false,
      hold: false,
      workflow: wf({
        design: "completed", appr_design: "completed", payment: "completed", appr_payment: "completed",
        jadwal: "completed", screen: "completed", helper: "completed", operator: "completed", sample: "completed",
        cetak: "completed", packing: "completed", sj: "completed", loading: "active",
      }),
    },
    {
      id: "spo-jadwal",
      po_number: "PO-JADWAL",
      order_date: "2026-09-04",
      customer: "PT Jadwal Nusantara",
      item_lines: ["Pouch"],
      status: "Penjadwalan",
      percent_delivered: 0,
      current_stage: 5,
      cancelled: false,
      hold: false,
      workflow: wf({
        design: "completed", appr_design: "completed", payment: "completed", appr_payment: "completed",
        jadwal: "pending",
      }),
    },
    {
      id: "spo-design",
      po_number: "PO-DESIGN",
      order_date: "2026-09-05",
      customer: "Studio Design One",
      item_lines: ["Sticker Roll"],
      status: "Design",
      percent_delivered: 0,
      current_stage: 1,
      cancelled: false,
      hold: false,
      workflow: wf({ design: "active" }),
    },
    {
      id: "spo-pay",
      po_number: "PO-PAY",
      order_date: "2026-09-06",
      customer: "PT Menunggu Payment",
      item_lines: ["Label Botol"],
      status: "Payment",
      percent_delivered: 0,
      current_stage: 3,
      cancelled: false,
      hold: false,
      workflow: wf({
        design: "completed", appr_design: "completed", payment: "active",
      }),
    },
    {
      id: "spo-screen",
      po_number: "PO-SCREEN",
      order_date: "2026-09-07",
      customer: "Screen House",
      item_lines: ["Screen Mesh"],
      status: "Screener",
      percent_delivered: 0,
      current_stage: 6,
      cancelled: false,
      hold: false,
      workflow: wf({
        design: "completed", appr_design: "completed", payment: "completed", appr_payment: "completed",
        jadwal: "completed", screen: "active",
      }),
    },
    {
      id: "spo-sample",
      po_number: "PO-SAMPLE",
      order_date: "2026-09-08",
      customer: "Sample Lab",
      item_lines: ["Sample Cup"],
      status: "Konfirmasi Sample",
      percent_delivered: 0,
      current_stage: 9,
      cancelled: false,
      hold: false,
      workflow: wf({
        design: "completed", appr_design: "completed", payment: "completed", appr_payment: "completed",
        jadwal: "completed", screen: "completed", helper: "completed", operator: "completed", sample: "active",
      }),
    },
    {
      id: "spo-cancel",
      po_number: "PO-CANCEL",
      order_date: "2026-08-20",
      customer: "PO Dibatalkan",
      item_lines: ["Cancelled Item"],
      status: "Cancelled",
      percent_delivered: 0,
      current_stage: 4,
      cancelled: true,
      hold: false,
      workflow: Object.fromEntries(STAGE_KEYS.map((k) => [k, "cancelled"])),
    },
    {
      id: "spo-kirim",
      po_number: "PO-KIRIM",
      order_date: "2026-08-10",
      customer: "Terkirim Lengkap",
      item_lines: ["Finished Pack"],
      status: "Terkirim",
      percent_delivered: 100,
      current_stage: 14,
      cancelled: false,
      hold: false,
      workflow: wf(Object.fromEntries(STAGE_KEYS.map((k) => [k, "completed"]))),
    },
    {
      id: "spo-gap",
      po_number: "PO-GAP",
      order_date: "2026-09-09",
      customer: "Evidence Gap",
      item_lines: ["Unknown Mix"],
      status: "Packing",
      percent_delivered: 0,
      current_stage: 11,
      cancelled: false,
      hold: false,
      workflow: wf({
        packing: "active",
        design: "pending",
        kirim: "unknown",
      }),
    },
  ];
}
