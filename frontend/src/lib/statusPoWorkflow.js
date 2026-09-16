/**
 * Display-only Status PO workflow matrix.
 * Maps existing lean status-po row fields (status, cancelled, hold, current_stage)
 * onto sequential cells. Does not change workflow progression.
 */

export const STATUS_PO_COL_STORAGE_KEY = "status-po-col-widths-v1";

export const STATUS_PO_WORKFLOW_STAGES = [
  { key: "order", header: "Order", full: "Order", color: "#1F4B7A" },
  { key: "design", header: "Design", full: "Design", color: "#7C4DFF" },
  { key: "appr_design", header: "Appr. Design", full: "Approval Design", color: "#8B5CF6" },
  { key: "payment", header: "Payment", full: "Payment", color: "#D89A00" },
  { key: "appr_payment", header: "Appr. Payment", full: "Approval Payment", color: "#22A447" },
  { key: "jadwal", header: "Jadwal", full: "Penjadwalan", color: "#2F80ED" },
  { key: "screen", header: "Screen", full: "Screener", color: "#18A6C9" },
  { key: "helper", header: "Helper", full: "Persiapan Helper", color: "#18A999" },
  { key: "operator", header: "Operator", full: "Operator", color: "#3F51B5" },
  { key: "sample", header: "Sample", full: "Konfirmasi Sample", color: "#8E44AD" },
  { key: "cetak", header: "Cetak", full: "Cetak", color: "#194D8C" },
  { key: "packing", header: "Packing", full: "Packing", color: "#F28C28" },
  { key: "sj", header: "SJ", full: "Surat Jalan", color: "#426A8C" },
  { key: "loading", header: "Loading", full: "Loading", color: "#4F6F8F" },
  { key: "kirim", header: "Kirim", full: "Pengiriman", color: "#26A65B" },
];

export const STATUS_PO_WORKFLOW_HEADERS = STATUS_PO_WORKFLOW_STAGES.map((s) => s.header);

export const STATUS_PO_IDENTITY_COLUMNS = ["No PO", "Tanggal", "Pelanggan", "Status Saat Ini"];

export const STATUS_PO_DEFAULT_WIDTHS = {
  check: 40,
  po: 175,
  date: 88,
  customer: 168,
  status: 132,
  order: 60,
  design: 60,
  appr_design: 76,
  payment: 64,
  appr_payment: 86,
  jadwal: 60,
  screen: 60,
  helper: 60,
  operator: 68,
  sample: 60,
  cetak: 60,
  packing: 64,
  sj: 52,
  loading: 64,
  kirim: 56,
  progress: 128,
  action: 64,
};

export const STATUS_PO_MIN_WIDTHS = {
  check: 36,
  po: 175,
  date: 88,
  customer: 150,
  status: 120,
  order: 60,
  design: 60,
  appr_design: 60,
  payment: 60,
  appr_payment: 60,
  jadwal: 60,
  screen: 60,
  helper: 60,
  operator: 60,
  sample: 60,
  cetak: 60,
  packing: 60,
  sj: 60,
  loading: 60,
  kirim: 60,
  progress: 120,
  action: 60,
};

const STATUS_TO_INDEX = {
  "order masuk": 0,
  order: 0,
  design: 1,
  "approval design": 2,
  payment: 3,
  "approval payment": 4,
  penjadwalan: 5,
  jadwal: 5,
  screener: 6,
  screen: 6,
  "persiapan helper": 7,
  helper: 7,
  operator: 8,
  "konfirmasi sample": 9,
  sample: 9,
  cetak: 10,
  "cetak berjalan": 10,
  "selesai cetak": 10,
  packing: 11,
  "packing berjalan": 11,
  "surat jalan": 12,
  "sj parsial": 12,
  sj: 12,
  loading: 13,
  pengiriman: 14,
  kirim: 14,
  "parsial terkirim": 14,
  terkirim: 15,
};

export function normalizeStatusLabel(status) {
  return String(status || "").trim().toLowerCase();
}

export function workflowStageIndex(row) {
  if (row?.cancelled || normalizeStatusLabel(row?.status) === "cancelled") return 0;
  const label = normalizeStatusLabel(row?.status);
  if (label === "hold") {
    const idx = Number(row?.current_stage);
    return Number.isFinite(idx) ? Math.max(0, Math.min(14, idx)) : 0;
  }
  if (label && Object.prototype.hasOwnProperty.call(STATUS_TO_INDEX, label)) {
    return STATUS_TO_INDEX[label];
  }
  const idx = Number(row?.current_stage);
  if (Number.isFinite(idx) && idx >= 0) return Math.min(15, idx);
  return 0;
}

export function workflowCellState(row, stageIndex) {
  const cancelled = Boolean(row?.cancelled) || normalizeStatusLabel(row?.status) === "cancelled";
  if (cancelled) return "cancelled";
  const hold = Boolean(row?.hold) || normalizeStatusLabel(row?.status) === "hold";
  const idx = workflowStageIndex(row);
  if (idx >= 15) return "done";
  if (stageIndex < idx) return "done";
  if (stageIndex === idx) return hold ? "hold" : "current";
  return "pending";
}

export function workflowCellTitle(state) {
  if (state === "done") return "Selesai";
  if (state === "current") return "Sedang berjalan";
  if (state === "pending") return "Belum dimulai";
  if (state === "hold") return "Tertahan";
  if (state === "cancelled") return "Dibatalkan";
  return "";
}

export function workflowProgressPercent(row) {
  if (Boolean(row?.cancelled) || normalizeStatusLabel(row?.status) === "cancelled") {
    const idx = Number(row?.current_stage);
    const n = Number.isFinite(idx) ? Math.max(0, Math.min(15, idx)) : 0;
    return Math.round((n / STATUS_PO_WORKFLOW_STAGES.length) * 100);
  }
  const idx = workflowStageIndex(row);
  if (idx >= STATUS_PO_WORKFLOW_STAGES.length) return 100;
  return Math.round((idx / STATUS_PO_WORKFLOW_STAGES.length) * 100);
}

export function workflowProgressColor(row) {
  if (Boolean(row?.cancelled) || normalizeStatusLabel(row?.status) === "cancelled") return "#C0392B";
  if (Boolean(row?.hold) || normalizeStatusLabel(row?.status) === "hold") return "#D89A00";
  const idx = Math.min(workflowStageIndex(row), STATUS_PO_WORKFLOW_STAGES.length - 1);
  return STATUS_PO_WORKFLOW_STAGES[idx]?.color || "#1F4B7A";
}

export function statusBadgeTone(status) {
  const label = normalizeStatusLabel(status);
  if (label === "cancelled") return "cancel";
  if (label === "hold") return "hold";
  const idx = STATUS_TO_INDEX[label];
  if (idx === undefined) return "neutral";
  if (idx >= 15 || idx === 14) return "kirim";
  return STATUS_PO_WORKFLOW_STAGES[Math.min(idx, 14)]?.key || "neutral";
}

export function clampColWidth(key, width) {
  const min = STATUS_PO_MIN_WIDTHS[key] || 40;
  const n = Number(width);
  if (!Number.isFinite(n)) return STATUS_PO_DEFAULT_WIDTHS[key] || min;
  return Math.max(min, Math.round(n));
}

export function readStoredColWidths() {
  if (typeof window === "undefined") return { ...STATUS_PO_DEFAULT_WIDTHS };
  try {
    const raw = window.localStorage.getItem(STATUS_PO_COL_STORAGE_KEY);
    if (!raw) return { ...STATUS_PO_DEFAULT_WIDTHS };
    const parsed = JSON.parse(raw);
    const next = { ...STATUS_PO_DEFAULT_WIDTHS };
    Object.keys(STATUS_PO_DEFAULT_WIDTHS).forEach((key) => {
      if (parsed && parsed[key] != null) next[key] = clampColWidth(key, parsed[key]);
    });
    return next;
  } catch {
    return { ...STATUS_PO_DEFAULT_WIDTHS };
  }
}

export function writeStoredColWidths(widths) {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(STATUS_PO_COL_STORAGE_KEY, JSON.stringify(widths));
  } catch {
    /* ignore quota */
  }
}

export function stickyOffsets(widths, { hasCheck, narrow } = {}) {
  const w = widths || STATUS_PO_DEFAULT_WIDTHS;
  const check = hasCheck ? (w.check || 40) : 0;
  const po = check;
  const date = po + (w.po || 175);
  const customer = date + (w.date || 88);
  const status = customer + (w.customer || 168);
  if (narrow) {
    return {
      check: 0,
      po,
      date: null,
      customer: po + (w.po || 175),
      status: null,
    };
  }
  return { check: 0, po, date, customer, status };
}
