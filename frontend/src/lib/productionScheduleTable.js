/** Canonical Jadwal Produksi table columns — shared by desktop /produksi and mobile /m/schedule. */

import { FILTER_DEFAULTS } from "./productionScheduleFilters";

/** Frozen inventory of every legitimate schedule column. Reorder allowed; deletion is not. */
export const PRODUCTION_SCHEDULE_REQUIRED_COLUMN_IDS = [
  "customer",
  "deadline",
  "deadline_ship",
  "work_date",
  "prio",
  "buffer",
  "po",
  "outlet",
  "qty",
  "qty_cetak",
  "item",
  "operator",
  "design",
  "payment",
  "hold",
  "screen",
  "helper",
  "packing",
  "sj",
  "driver",
  "status",
  "screen_size",
];

/**
 * Spreadsheet column order shared by /produksi and /m/schedule.
 * Item sits after Operator; Total Qty sits after Qty Cetak.
 */
export const PRODUCTION_SCHEDULE_TABLE_COLS = [
  { key: "po", id: "po", label: "No PO", group: "identity", align: "left", type: "id", baseWidth: 112, minWidth: 76, filter: "po" },
  { key: "customer", id: "customer", label: "Customer", group: "identity", align: "left", type: "text", baseWidth: 168, minWidth: 96, filter: "customer", wrap: true },
  { key: "work_date", id: "work_date", label: "Tgl Pengerjaan", group: "planning", align: "left", type: "date", baseWidth: 118, minWidth: 88, testid: "prod-th-work-date", filter: "work_date", sort: "work_date", wrap: true },
  { key: "operator", id: "operator", label: "Operator", group: "assignment", align: "left", type: "text", baseWidth: 168, minWidth: 96, filter: "operator", wrap: true },
  { key: "item", id: "item", label: "Item", group: "identity", align: "left", type: "text", baseWidth: 168, minWidth: 96, wrap: true },
  { key: "qty_cetak", id: "qty_cetak", label: "Qty Cetak", group: "quantity", align: "right", type: "qty", baseWidth: 128, minWidth: 88, num: true },
  { key: "qty", id: "qty", label: "Total Qty", group: "quantity", align: "right", type: "qty", baseWidth: 104, minWidth: 72, num: true },
  { key: "prio", id: "prio", label: "Prio", group: "planning", align: "left", type: "status", baseWidth: 78, minWidth: 56, filter: "prio" },
  { key: "buffer", id: "buffer", label: "Buffer", group: "planning", align: "left", type: "status", baseWidth: 72, minWidth: 52, testid: "prod-th-buffer", filter: "buffer" },
  { key: "deadline", id: "deadline", label: "Tanggal Pengiriman", group: "planning", align: "left", type: "date", baseWidth: 118, minWidth: 88, filter: "deadline", sort: "deadline" },
  { key: "deadline_ship", id: "deadline_ship", label: "Sumber Jadwal", group: "planning", align: "left", type: "date", baseWidth: 168, minWidth: 110, testid: "prod-th-deadline-effective", wrap: true },
  { key: "outlet", id: "outlet", label: "Outlet", group: "identity", align: "left", type: "text", baseWidth: 140, minWidth: 88, wrap: true },
  { key: "screen_size", id: "screen_size", label: "Screen", group: "assignment", align: "left", type: "status", baseWidth: 108, minWidth: 80, wrap: true },
  { key: "screen", id: "screen", label: "Persiapan Screen", group: "quantity", align: "right", type: "qty", baseWidth: 108, minWidth: 80, num: true, filter: "screener" },
  { key: "design", id: "design", label: "Design", group: "assignment", align: "left", type: "status", baseWidth: 96, minWidth: 72 },
  { key: "payment", id: "payment", label: "Payment", group: "assignment", align: "left", type: "status", baseWidth: 96, minWidth: 72 },
  { key: "hold", id: "hold", label: "HOLD", group: "assignment", align: "left", type: "status", baseWidth: 68, minWidth: 52, filter: "hold" },
  { key: "helper", id: "helper", label: "Persiapan Cup", group: "assignment", align: "right", type: "qty", baseWidth: 108, minWidth: 80, num: true, filter: "helper", wrap: true },
  { key: "packing", id: "packing", label: "Packing", group: "fulfillment", align: "left", type: "status", baseWidth: 84, minWidth: 64, filter: "packing" },
  { key: "sj", id: "sj", label: "Surat Jalan", group: "fulfillment", align: "left", type: "text", baseWidth: 118, minWidth: 80, wrap: true },
  { key: "driver", id: "driver", label: "Driver", group: "fulfillment", align: "left", type: "text", baseWidth: 118, minWidth: 80, wrap: true },
  { key: "status", id: "status", label: "Status Akhir", group: "fulfillment", align: "left", type: "status", baseWidth: 108, minWidth: 80, filter: "status", wrap: true },
];

export const PRODUCTION_SCHEDULE_COLUMN_IDS = PRODUCTION_SCHEDULE_TABLE_COLS.map((col) => col.key);

/** Desktop /produksi display only — Driver + Surat Jalan stay in the canonical inventory. */
export const DESKTOP_PRODUCTION_SCHEDULE_HIDDEN_KEYS = ["sj", "driver"];

export const DESKTOP_PRODUCTION_SCHEDULE_GROUPS = [
  { id: "identity", label: "Identitas PO", keys: ["po", "customer"] },
  { id: "planning", label: "Penjadwalan", keys: ["work_date", "operator"] },
  { id: "production", label: "Produksi", keys: ["item", "qty_cetak", "qty", "prio", "buffer", "deadline", "deadline_ship", "outlet", "screen_size"] },
  { id: "progress", label: "Progress Produksi", keys: ["screen", "design", "payment", "hold", "helper", "packing"] },
  { id: "status", label: "Status", keys: ["status"] },
];

export const DESKTOP_PRODUCTION_SCHEDULE_COL_KEYS = DESKTOP_PRODUCTION_SCHEDULE_GROUPS.flatMap((g) => g.keys);

export function buildDesktopProductionScheduleCols(source = PRODUCTION_SCHEDULE_TABLE_COLS) {
  const byKey = new Map(source.map((col) => [col.key, col]));
  return DESKTOP_PRODUCTION_SCHEDULE_GROUPS.flatMap((group, groupIdx) => (
    group.keys.map((key, keyIdx) => {
      const col = byKey.get(key);
      if (!col) return null;
      const next = { ...col, desktopGroup: group.id, desktopGroupLabel: group.label };
      if (key === "po") next.sticky = "po";
      if (key === "customer") next.sticky = "customer";
      if (key === "item") {
        next.wrap = false;
        next.baseWidth = 148;
        next.maxWidth = 168;
      }
      if (keyIdx === 0 && groupIdx > 0) next.groupStart = true;
      return next;
    })
  )).filter(Boolean);
}

export const DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS = buildDesktopProductionScheduleCols();

export function desktopProductionScheduleGroupSpans(cols = DESKTOP_PRODUCTION_SCHEDULE_TABLE_COLS) {
  const spans = [];
  cols.forEach((col) => {
    const last = spans[spans.length - 1];
    if (last && last.id === col.desktopGroup) {
      last.span += 1;
      last.keys.push(col.key);
      return;
    }
    spans.push({
      id: col.desktopGroup,
      label: col.desktopGroupLabel,
      span: 1,
      keys: [col.key],
      sticky: col.sticky === "po" ? "identity" : null,
    });
  });
  return spans;
}

export function productionDashboardRequestParams(filters = {}) {
  const f = { ...FILTER_DEFAULTS, ...filters };
  return {
    include_cancelled: Boolean(f.includeCancelled),
    include_completed: Boolean(f.includeCompleted) || f.workDateFilter === "hari_ini",
    // Additive backend path: same `_production_row` columns, no file-byte documents.
    view: "summary",
  };
}

export function productionSheetColumnStyle(col) {
  const width = Number(col?.baseWidth) || 96;
  const minWidth = Number(col?.minWidth) || 56;
  return {
    width: `calc(${width}px * var(--production-sheet-zoom, 1))`,
    minWidth: `calc(${minWidth}px * var(--production-sheet-zoom, 1))`,
  };
}

export function productionSheetCellClassName(col, extra = "") {
  const parts = ["production-sheet-cell"];
  if (col?.num || col?.align === "right" || col?.type === "qty") {
    parts.push("num", "production-sheet-cell-num");
  }
  if (col?.wrap) parts.push("production-sheet-cell-wrap");
  if (col?.type === "status") parts.push("production-sheet-cell-status");
  if (col?.sticky === "po") parts.push("prod-sticky-col", "prod-sticky-po");
  if (col?.sticky === "customer") parts.push("prod-sticky-col", "prod-sticky-customer");
  if (col?.groupStart) parts.push("prod-group-start");
  if (col?.desktopGroup) parts.push(`prod-col-group-${col.desktopGroup}`);
  if (extra) parts.push(extra);
  return parts.filter(Boolean).join(" ");
}
