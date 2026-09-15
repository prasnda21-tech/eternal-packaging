/** PWA-only Jadwal Produksi columns — display subset; desktop Web keeps full set. */

import { PRODUCTION_SCHEDULE_TABLE_COLS } from "./productionScheduleTable";

const MOBILE_HIDDEN_KEYS = new Set([
  "payment",
  "packing",
  "driver",
  "screen_size",
  "po",
  "operator",
  "buffer",
  "deadline",
  "deadline_ship",
  "outlet",
  "design",
  "hold",
  "status",
]);

const OPS_COL_ORDER = [
  "prio", "work_date", "customer", "sj", "item", "qty",
  "helper", "screen", "qty_cetak", "packing", "loading", "shipped",
];

const EXTRA_STATUS_COLS = {
  loading: { key: "loading", label: "Load", baseWidth: 52, minWidth: 48, num: true },
  shipped: { key: "shipped", label: "Kirim", baseWidth: 52, minWidth: 48, num: true },
};

const OPS_COL_PATCH = {
  prio: { label: "Prioritas", baseWidth: 92, minWidth: 76 },
  work_date: { label: "Tgl Kerja", baseWidth: 88, minWidth: 72 },
  customer: { label: "Customer / No PO", baseWidth: 248, minWidth: 196, wrap: true },
  sj: { label: "Surat Jalan", baseWidth: 108, minWidth: 84, wrap: true },
  item: { label: "Item Line", baseWidth: 248, minWidth: 180, wrap: true },
  qty: { label: "Qty Total", baseWidth: 84, minWidth: 64 },
  helper: { label: "Cup", baseWidth: 48, minWidth: 44 },
  screen: { label: "Screen", filter: "screener", baseWidth: 52, minWidth: 44 },
  qty_cetak: { label: "Cetak", baseWidth: 48, minWidth: 44 },
  packing: { label: "Pack", baseWidth: 48, minWidth: 44, num: true },
  loading: { label: "Load", baseWidth: 48, minWidth: 44 },
  shipped: { label: "Kirim", baseWidth: 48, minWidth: 44 },
};

/**
 * Compact PWA operational columns:
 * Prioritas · Tgl Kerja · Customer/PO · Item Line · Qty · Cup · Screen · Cetak
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
  "operator",
  "buffer",
  "deadline",
  "deadline_ship",
  "outlet",
  "design",
  "hold",
  "status",
];

export { MOBILE_HIDDEN_KEYS };
