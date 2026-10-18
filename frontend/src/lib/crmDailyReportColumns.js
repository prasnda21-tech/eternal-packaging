/** Local-only column layout prefs for Laporan Harian CRM list. */

export const CRM_DAILY_COL_STORAGE_KEY = "crm-daily-report-cols-v1";

export const CRM_DAILY_DEFAULT_COL_ORDER = [
  "no",
  "tanggal",
  "crm",
  "customer",
  "wa",
  "po",
  "sales",
  "status_order",
  "status_customer",
  "no_order_reason",
  "complaint",
  "issue",
  "actions",
];

export const CRM_DAILY_COL_LABELS = {
  no: "No",
  tanggal: "Tanggal",
  crm: "CRM",
  customer: "Customer/Outlet",
  wa: "No WA",
  po: "No PO",
  sales: "Sales",
  status_order: "Status Order",
  status_customer: "Status Customer",
  no_order_reason: "Keterangan Tidak Order",
  complaint: "Komplain",
  issue: "Deskripsi Kendala",
  actions: "Aksi",
};

/** Defaults sized so primary header labels stay readable with drag/menu chrome. */
export const CRM_DAILY_DEFAULT_COL_WIDTHS = {
  no: 48,
  tanggal: 108,
  crm: 120,
  customer: 200,
  wa: 128,
  po: 136,
  sales: 108,
  status_order: 128,
  status_customer: 136,
  no_order_reason: 180,
  complaint: 112,
  issue: 180,
  actions: 128,
};

/** Floor widths — old localStorage prefs below these are clamped on read. */
export const CRM_DAILY_MIN_COL_WIDTHS = {
  no: 44,
  tanggal: 100,
  crm: 96,
  customer: 160,
  wa: 112,
  po: 112,
  sales: 96,
  status_order: 120,
  status_customer: 128,
  no_order_reason: 148,
  complaint: 100,
  issue: 148,
  actions: 116,
};

export function clampCrmDailyColWidth(key, width) {
  const min = CRM_DAILY_MIN_COL_WIDTHS[key] ?? 40;
  const fallback = CRM_DAILY_DEFAULT_COL_WIDTHS[key] ?? min;
  const n = Number(width);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.round(n));
}

export function normalizeCrmDailyColOrder(raw) {
  const seen = new Set();
  const next = [];
  const candidates = Array.isArray(raw) ? raw : [];
  candidates.forEach((key) => {
    if (CRM_DAILY_DEFAULT_COL_ORDER.includes(key) && !seen.has(key)) {
      seen.add(key);
      next.push(key);
    }
  });
  CRM_DAILY_DEFAULT_COL_ORDER.forEach((key) => {
    if (!seen.has(key)) next.push(key);
  });
  return next;
}

export function normalizeCrmDailyColWidths(raw) {
  const next = { ...CRM_DAILY_DEFAULT_COL_WIDTHS };
  if (!raw || typeof raw !== "object") return next;
  Object.keys(CRM_DAILY_DEFAULT_COL_WIDTHS).forEach((key) => {
    if (raw[key] != null) next[key] = clampCrmDailyColWidth(key, raw[key]);
  });
  return next;
}

export function readCrmDailyColPrefs(storage = typeof window !== "undefined" ? window.localStorage : null) {
  const fallback = {
    order: [...CRM_DAILY_DEFAULT_COL_ORDER],
    widths: { ...CRM_DAILY_DEFAULT_COL_WIDTHS },
  };
  if (!storage) return fallback;
  try {
    const raw = storage.getItem(CRM_DAILY_COL_STORAGE_KEY);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw);
    return {
      order: normalizeCrmDailyColOrder(parsed?.order),
      widths: normalizeCrmDailyColWidths(parsed?.widths),
    };
  } catch {
    return fallback;
  }
}

export function writeCrmDailyColPrefs(prefs, storage = typeof window !== "undefined" ? window.localStorage : null) {
  if (!storage) return;
  try {
    storage.setItem(CRM_DAILY_COL_STORAGE_KEY, JSON.stringify({
      order: normalizeCrmDailyColOrder(prefs?.order),
      widths: normalizeCrmDailyColWidths(prefs?.widths),
    }));
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearCrmDailyColPrefs(storage = typeof window !== "undefined" ? window.localStorage : null) {
  if (!storage) return;
  try {
    storage.removeItem(CRM_DAILY_COL_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function moveCrmDailyColumn(order, fromKey, toKey) {
  const next = normalizeCrmDailyColOrder(order);
  const from = next.indexOf(fromKey);
  const to = next.indexOf(toKey);
  if (from < 0 || to < 0 || from === to) return next;
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item);
  return next;
}

export function shiftCrmDailyColumn(order, key, direction) {
  const next = normalizeCrmDailyColOrder(order);
  const idx = next.indexOf(key);
  if (idx < 0) return next;
  const target = direction < 0 ? idx - 1 : idx + 1;
  if (target < 0 || target >= next.length) return next;
  const copy = [...next];
  const tmp = copy[idx];
  copy[idx] = copy[target];
  copy[target] = tmp;
  return copy;
}

export function crmDailyTableMinWidth(widths) {
  const w = widths || CRM_DAILY_DEFAULT_COL_WIDTHS;
  return CRM_DAILY_DEFAULT_COL_ORDER.reduce((sum, key) => sum + (w[key] || CRM_DAILY_DEFAULT_COL_WIDTHS[key] || 0), 0);
}
