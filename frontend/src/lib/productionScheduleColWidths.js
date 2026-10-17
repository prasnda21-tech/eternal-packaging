/** Resizable column widths for desktop Jadwal Produksi sheet (browser-local, per user). */

import { DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS } from "./productionScheduleTable";

export const PRODUCTION_SCHEDULE_COL_STORAGE_PREFIX = "eternal.ui.produksi-schedule-col-widths.v1";
export const PRODUCTION_SCHEDULE_TABLE_ID = "desktop-produksi-schedule";

export function productionScheduleColWidthsStorageKey(userId) {
  const uid = userId != null && String(userId).trim() !== "" ? String(userId).trim() : "";
  return uid
    ? `${PRODUCTION_SCHEDULE_COL_STORAGE_PREFIX}.${uid}`
    : PRODUCTION_SCHEDULE_COL_STORAGE_PREFIX;
}

function buildDefaultsFromCols(cols = DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS) {
  const defaults = {};
  const mins = {};
  const maxs = {};
  cols.forEach((col) => {
    const key = col.key;
    const base = Number(col.baseWidth) || 96;
    const min = Number(col.minWidth) || Math.min(56, base);
    const max = Number(col.maxWidth) || Math.max(base * 2.5, base + 80);
    defaults[key] = base;
    mins[key] = min;
    // Identity columns may grow wider; qty columns stay tighter.
    if (col.type === "qty" || col.num) {
      maxs[key] = Math.min(max, Math.max(base + 40, 220));
    } else if (col.sticky) {
      maxs[key] = Math.max(max, 320);
    } else {
      maxs[key] = Math.max(max, 280);
    }
  });
  return { defaults, mins, maxs };
}

const BUILT = buildDefaultsFromCols();

export const PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS = BUILT.defaults;
export const PRODUCTION_SCHEDULE_MIN_COL_WIDTHS = BUILT.mins;
export const PRODUCTION_SCHEDULE_MAX_COL_WIDTHS = BUILT.maxs;

export function clampProductionScheduleColWidth(key, width) {
  const min = PRODUCTION_SCHEDULE_MIN_COL_WIDTHS[key] ?? 48;
  const max = PRODUCTION_SCHEDULE_MAX_COL_WIDTHS[key] ?? 360;
  const fallback = PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS[key] ?? min;
  const n = Number(width);
  if (!Number.isFinite(n)) return fallback;
  return Math.max(min, Math.min(max, Math.round(n)));
}

export function normalizeProductionScheduleColWidths(raw) {
  const next = { ...PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS };
  if (!raw || typeof raw !== "object") return next;
  Object.keys(PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS).forEach((key) => {
    if (raw[key] != null) next[key] = clampProductionScheduleColWidth(key, raw[key]);
  });
  return next;
}

export function readProductionScheduleColWidths(
  userId,
  storage = typeof window !== "undefined" ? window.localStorage : null,
) {
  if (!storage) return { ...PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS };
  try {
    const raw = storage.getItem(productionScheduleColWidthsStorageKey(userId));
    if (!raw) return { ...PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS };
    return normalizeProductionScheduleColWidths(JSON.parse(raw));
  } catch {
    return { ...PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS };
  }
}

export function writeProductionScheduleColWidths(
  widths,
  userId,
  storage = typeof window !== "undefined" ? window.localStorage : null,
) {
  if (!storage) return;
  try {
    storage.setItem(
      productionScheduleColWidthsStorageKey(userId),
      JSON.stringify(normalizeProductionScheduleColWidths(widths)),
    );
  } catch {
    /* quota / private mode */
  }
}

export function clearProductionScheduleColWidths(
  userId,
  storage = typeof window !== "undefined" ? window.localStorage : null,
) {
  if (!storage) return;
  try {
    storage.removeItem(productionScheduleColWidthsStorageKey(userId));
  } catch {
    /* ignore */
  }
}

export function productionScheduleStickyCssVars(widths) {
  const w = widths || PRODUCTION_SCHEDULE_DEFAULT_COL_WIDTHS;
  const po = clampProductionScheduleColWidth("po", w.po);
  const customer = clampProductionScheduleColWidth("customer", w.customer);
  return {
    "--prod-sticky-po-w": `calc(${po}px * var(--production-sheet-zoom, 1))`,
    "--prod-sticky-customer-w": `calc(${customer}px * var(--production-sheet-zoom, 1))`,
  };
}
