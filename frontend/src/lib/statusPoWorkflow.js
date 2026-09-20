/**
 * Display-only Status PO workflow matrix.
 * Cells render explicit `row.workflow[stage]` from the Status PO payload.
 */

export const STATUS_PO_COL_STORAGE_KEY = "status-po-col-widths-v3";

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

export const STATUS_PO_IDENTITY_COLUMNS = ["No PO", "Tanggal PO", "Pelanggan", "Item / Produk", "Status Saat Ini"];

export const STATUS_PO_TABLE_KEYS = [
  "check", "po", "date", "customer", "items", "status", "age", "progress", "pct", "action",
];

export const STATUS_PO_DEFAULT_WIDTHS = {
  check: 40,
  po: 168,
  date: 92,
  customer: 168,
  items: 188,
  status: 188,
  age: 72,
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
  progress: 214,
  pct: 52,
  action: 52,
};

export const STATUS_PO_MIN_WIDTHS = {
  check: 36,
  po: 132,
  date: 76,
  customer: 112,
  items: 140,
  status: 148,
  age: 60,
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
  progress: 168,
  pct: 44,
  action: 44,
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

export function workflowCellState(row, stageKey) {
  const cancelled = Boolean(row?.cancelled) || normalizeStatusLabel(row?.status) === "cancelled";
  if (cancelled) return "cancelled";
  const key = typeof stageKey === "number"
    ? STATUS_PO_WORKFLOW_STAGES[stageKey]?.key
    : stageKey;
  const raw = row?.workflow?.[key];
  if (raw === "completed") return "completed";
  if (raw === "skipped" || raw === "na" || raw === "not_applicable") return "skipped";
  if (raw === "active") return "active";
  if (raw === "pending") return "pending";
  if (raw === "hold") return "hold";
  if (raw === "cancelled") return "cancelled";
  return "unknown";
}

const DONE_CELL_STATES = new Set(["completed", "skipped", "na", "not_applicable"]);

/** 0-based canonical stage from the same workflow evidence as the 15 boxes. 15 = all done. */
export function workflowCanonicalStageIndex(row) {
  if (row?.cancelled || normalizeStatusLabel(row?.status) === "cancelled") return 0;
  const label = normalizeStatusLabel(row?.status);
  if (label === "terkirim") return 15;
  if (label === "parsial terkirim" || label === "pengiriman") return 14;
  if (label === "loading") return 13;
  if (label === "surat jalan" || label === "sj parsial") return 12;
  const wf = row?.workflow;
  const keys = STATUS_PO_WORKFLOW_STAGES.map((s) => s.key);
  if (wf && typeof wf === "object") {
    const holdIdx = keys.findIndex((k) => wf[k] === "hold");
    if (holdIdx >= 0) return holdIdx;
    const allDone = keys.length > 0 && keys.every((k) => DONE_CELL_STATES.has(wf[k]));
    if (allDone) return 15;
    if (DONE_CELL_STATES.has(wf.kirim)) return 15;
    if (wf.kirim === "active") return 14;
    if (DONE_CELL_STATES.has(wf.loading) && wf.kirim === "pending") return 14;
    let latestActive = -1;
    keys.forEach((k, i) => {
      if (wf[k] === "active") latestActive = i;
    });
    if (latestActive >= 0) return latestActive;
    let lastDone = -1;
    keys.forEach((k, i) => {
      if (DONE_CELL_STATES.has(wf[k])) lastDone = i;
    });
    if (lastDone >= 0) return lastDone;
  }
  return workflowStageIndex(row);
}

export function workflowCellTitle(state, reason) {
  if (reason === "legacy_no_loading_evidence") {
    return "Dilewati (data lama, bukti Loading tidak tercatat)";
  }
  if (state === "completed" || state === "done") return "Selesai";
  if (state === "skipped") return "Dilewati (data lama)";
  if (state === "active" || state === "current") return "Sedang berjalan";
  if (state === "pending") return "Belum dimulai";
  if (state === "hold") return "Tertahan";
  if (state === "cancelled") return "Dibatalkan";
  return "Tidak dapat ditentukan";
}

export function workflowCellCaption(stage, state, reason) {
  const idx = STATUS_PO_WORKFLOW_STAGES.findIndex((s) => s.key === stage?.key);
  const number = idx >= 0 ? idx + 1 : "";
  const name = stage?.full || stage?.header || "";
  return `${number}. ${name} — ${workflowCellTitle(state, reason)}`;
}

export function workflowProgressPercent(row) {
  const wf = row?.workflow;
  if (!wf || typeof wf !== "object") return 0;
  const keys = STATUS_PO_WORKFLOW_STAGES.map((s) => s.key);
  const done = keys.filter((k) => DONE_CELL_STATES.has(wf[k])).length;
  return Math.round((done / keys.length) * 100);
}

export function workflowProgressColor(row) {
  if (Boolean(row?.cancelled) || normalizeStatusLabel(row?.status) === "cancelled") return "#C0392B";
  if (Boolean(row?.hold) || normalizeStatusLabel(row?.status) === "hold") return "#F44747";
  const wf = row?.workflow || {};
  const activeKey = STATUS_PO_WORKFLOW_STAGES.find((s) => wf[s.key] === "active" || wf[s.key] === "hold")?.key;
  if (activeKey) {
    if (wf[activeKey] === "hold") return "#F44747";
    return STATUS_PO_WORKFLOW_STAGES.find((s) => s.key === activeKey)?.color || "#1F4B7A";
  }
  const lastDone = [...STATUS_PO_WORKFLOW_STAGES].reverse().find((s) => DONE_CELL_STATES.has(wf[s.key]));
  return lastDone?.color || "#1F4B7A";
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

/** 1-based stage number from the same workflow evidence as boxes and percent. */
export function statusStageDisplayNumber(row) {
  const idx = workflowCanonicalStageIndex(row);
  if (!Number.isFinite(idx) || idx < 0) return 1;
  if (idx >= 15) return 15;
  return idx + 1;
}

/**
 * Compact status-strip color from existing row flags/labels.
 * Does not invent new workflow meaning.
 */
export function statusStripTone(row) {
  const label = normalizeStatusLabel(row?.status);
  if (Boolean(row?.cancelled) || label === "cancelled") return "problem";
  if (Boolean(row?.hold) || label === "hold") return "problem";
  const wf = row?.workflow;
  const keys = STATUS_PO_WORKFLOW_STAGES.map((s) => s.key);
  if (wf && typeof wf === "object" && keys.every((k) => DONE_CELL_STATES.has(wf[k]))) return "done";
  if (label === "terkirim") return "done";
  if (
    label === "penjadwalan"
    || label.includes("berjalan")
    || label === "parsial terkirim"
    || label === "sj parsial"
  ) return "process";
  const idx = workflowCanonicalStageIndex(row);
  if (idx >= 15) return "done";
  if (idx <= 0) return "pending";
  return "active";
}

/** Map explicit workflow cell state to a 15-segment color token. */
export function progressSegmentTone(state, reason) {
  if (state === "skipped" || reason === "legacy_no_loading_evidence") return "skipped";
  if (state === "completed" || state === "done") return "done";
  if (state === "active" || state === "current") return "active";
  if (state === "hold") return "problem";
  if (state === "cancelled") return "problem";
  return "pending";
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
  const d = STATUS_PO_DEFAULT_WIDTHS;
  const check = hasCheck ? (w.check || d.check) : 0;
  const po = check;
  const date = po + (w.po || d.po);
  const customer = date + (w.date || d.date);
  if (narrow) {
    return {
      check: 0,
      po,
      date: null,
      customer: po + (w.po || d.po),
      items: null,
      status: null,
      age: null,
      progress: null,
      pct: null,
      action: null,
    };
  }
  return {
    check: 0,
    po,
    date,
    customer,
    items: null,
    status: null,
    age: null,
    progress: null,
    pct: null,
    action: null,
  };
}
