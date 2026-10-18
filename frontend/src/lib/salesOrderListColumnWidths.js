/** Resizable column widths for Pesanan Penjualan list (browser-local only). */

export const SALES_ORDER_COL_STORAGE_KEY = "pesanan-penjualan-col-widths-v1";

export const SALES_ORDER_RESIZABLE_KEYS = [
  "customer",
  "orderDate",
  "shipDate",
  "qty",
  "progress",
  "status",
  "sales",
  "creator",
  "invoice",
  "actions",
];

/** Compact defaults aligned with approved desktop preview. */
export const SALES_ORDER_DEFAULT_COL_WIDTHS = {
  check: 36,
  customer: 240,
  orderDate: 92,
  shipDate: 92,
  qty: 86,
  progress: 132,
  status: 100,
  sales: 96,
  creator: 110,
  invoice: 120,
  actions: 120,
};

export const SALES_ORDER_MIN_COL_WIDTHS = {
  check: 36,
  customer: 120,
  orderDate: 72,
  shipDate: 72,
  qty: 56,
  progress: 90,
  status: 72,
  sales: 64,
  creator: 80,
  invoice: 90,
  actions: 100,
};

export function clampSalesOrderColWidth(key, width) {
  const min = SALES_ORDER_MIN_COL_WIDTHS[key] ?? 40;
  const fallback = SALES_ORDER_DEFAULT_COL_WIDTHS[key] ?? min;
  const n = Number(width);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.round(n));
}

export function normalizeSalesOrderColWidths(raw) {
  const next = { ...SALES_ORDER_DEFAULT_COL_WIDTHS };
  if (!raw || typeof raw !== "object") return next;
  Object.keys(SALES_ORDER_DEFAULT_COL_WIDTHS).forEach((key) => {
    if (raw[key] != null) next[key] = clampSalesOrderColWidth(key, raw[key]);
  });
  return next;
}

export function readSalesOrderColWidths(storage = typeof window !== "undefined" ? window.localStorage : null) {
  if (!storage) return { ...SALES_ORDER_DEFAULT_COL_WIDTHS };
  try {
    const raw = storage.getItem(SALES_ORDER_COL_STORAGE_KEY);
    if (!raw) return { ...SALES_ORDER_DEFAULT_COL_WIDTHS };
    return normalizeSalesOrderColWidths(JSON.parse(raw));
  } catch {
    return { ...SALES_ORDER_DEFAULT_COL_WIDTHS };
  }
}

export function writeSalesOrderColWidths(widths, storage = typeof window !== "undefined" ? window.localStorage : null) {
  if (!storage) return;
  try {
    storage.setItem(SALES_ORDER_COL_STORAGE_KEY, JSON.stringify(normalizeSalesOrderColWidths(widths)));
  } catch {
    /* ignore quota / private mode */
  }
}

export function clearSalesOrderColWidths(storage = typeof window !== "undefined" ? window.localStorage : null) {
  if (!storage) return;
  try {
    storage.removeItem(SALES_ORDER_COL_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function salesOrderTableMinWidth(widths, { includeCheck = false, includeInvoice = true } = {}) {
  const w = widths || SALES_ORDER_DEFAULT_COL_WIDTHS;
  const keys = [
    ...(includeCheck ? ["check"] : []),
    "customer",
    "orderDate",
    "shipDate",
    "qty",
    "progress",
    "status",
    "sales",
    "creator",
    ...(includeInvoice ? ["invoice"] : []),
    "actions",
  ];
  return keys.reduce((sum, key) => sum + (w[key] || SALES_ORDER_DEFAULT_COL_WIDTHS[key] || 60), 0);
}
