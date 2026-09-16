/** PWA-only Jadwal Produksi columns — display subset; desktop Web keeps full set. */

import { PRODUCTION_SCHEDULE_TABLE_COLS } from "./productionScheduleTable";

const MOBILE_HIDDEN_KEYS = new Set([
  "payment",
  "packing",
  "driver",
  "screen_size",
  "po",
  "sj",
  "qty",
  "buffer",
  "deadline",
  "deadline_ship",
  "outlet",
  "design",
  "hold",
  "status",
]);

const OPS_COL_ORDER = [
  "prio", "work_date", "customer", "operator", "item",
  "helper", "screen", "qty_cetak", "packing", "loading", "shipped",
];

const EXTRA_STATUS_COLS = {
  loading: { key: "loading", label: "Load", baseWidth: 52, minWidth: 48, num: true },
  shipped: { key: "shipped", label: "Kirim", baseWidth: 52, minWidth: 48, num: true },
};

const OPS_COL_PATCH = {
  prio: { label: "Prioritas", baseWidth: 76, minWidth: 64 },
  work_date: { label: "Tgl Kerja", baseWidth: 72, minWidth: 64 },
  customer: { label: "Customer / No PO", baseWidth: 156, minWidth: 128, wrap: true },
  operator: { label: "Operator", baseWidth: 108, minWidth: 88, wrap: true },
  item: { label: "Item Line", baseWidth: 112, minWidth: 88, wrap: true },
  helper: { label: "Cup", baseWidth: 40, minWidth: 36 },
  screen: { label: "Screen", filter: "screener", baseWidth: 44, minWidth: 36 },
  qty_cetak: { label: "Cetak", baseWidth: 40, minWidth: 36 },
  packing: { label: "Pack", baseWidth: 40, minWidth: 36, num: true },
  loading: { label: "Load", baseWidth: 40, minWidth: 36 },
  shipped: { label: "Kirim", baseWidth: 40, minWidth: 36 },
};

/**
 * Compact PWA operational columns:
 * Prioritas · Tgl Kerja · Customer/PO · Operator · Item Line · Cup · Screen · Cetak
 */
export function buildMobileProductionScheduleCols(source = PRODUCTION_SCHEDULE_TABLE_COLS) {
  const byKey = new Map(source.map((c) => [c.key, { ...c }]));
  if (source.length) {
    Object.values(EXTRA_STATUS_COLS).forEach((col) => byKey.set(col.key, { ...col }));
  }
  return OPS_COL_ORDER.map((key) => {
    const col = byKey.get(key);
    if (!col) return null;
    return { ...col, ...(OPS_COL_PATCH[key] || {}) };
  }).filter(Boolean);
}

export const MOBILE_PRODUCTION_SCHEDULE_TABLE_COLS = buildMobileProductionScheduleCols();

export const MOBILE_PRODUCTION_STATUS_COL_KEYS = [
  "helper", "screen", "qty_cetak", "packing", "loading", "shipped",
];

export const MOBILE_PRODUCTION_ABSENT_KEYS = [
  "payment",
  "driver",
  "screen_size",
  "po",
  "sj",
  "qty",
  "buffer",
  "deadline",
  "deadline_ship",
  "outlet",
  "design",
  "hold",
  "status",
];

export { MOBILE_HIDDEN_KEYS };
