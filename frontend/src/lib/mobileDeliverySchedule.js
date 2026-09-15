/** PWA Jadwal Pengiriman — same view/edit perms as Web DeliverySchedulePage. */

import { NAV_KEYS } from "./navAccess";

export const MOBILE_DELIVERY_SCHEDULE_VIEW_PERMS = [
  NAV_KEYS.deliverySchedule,
  "delivery.schedule.view",
  "schedule.view",
];

export const MOBILE_DELIVERY_SCHEDULE_EDIT_PERM = "delivery.location.manage";

/** Assign/change driver on Jadwal / Route Review — same schedule-edit gate as location. */
export const MOBILE_DELIVERY_ASSIGN_DRIVER_PERM = "delivery.location.manage";

export const MOBILE_DELIVERY_SCHEDULE_PATH = "/m/delivery-schedule";

export function canViewMobileDeliverySchedule(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return MOBILE_DELIVERY_SCHEDULE_VIEW_PERMS.some((perm) => hasPerm(perm));
}

export function canEditMobileDeliveryScheduleLocation(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return hasPerm(MOBILE_DELIVERY_SCHEDULE_EDIT_PERM);
}

export function canAssignDeliveryDriver(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return hasPerm(MOBILE_DELIVERY_ASSIGN_DRIVER_PERM);
}

export function mobileDeliveryScheduleHref() {
  return MOBILE_DELIVERY_SCHEDULE_PATH;
}

export function mobileDeliveryOrderHref(orderId) {
  if (!orderId) return "/m/orders";
  return `/m/orders/${encodeURIComponent(orderId)}`;
}

export function mobileDeliveryWorkHref(orderId) {
  if (!orderId) return "/m/orders";
  return `/m/orders/${encodeURIComponent(orderId)}?ws=delivery`;
}

/** Compact PWA operational columns — Customer stacks PO/SJ; item lines keep qty. */
export const MOBILE_DELIVERY_SCHEDULE_COLS = [
  { key: "prio", label: "Prioritas", width: 92 },
  { key: "effective_delivery_date", label: "Tgl Kirim", width: 88, sort: "effective_delivery_date" },
  { key: "customer", label: "Customer / No PO", width: 248, sort: "customer" },
  { key: "item_line", label: "Item Line", width: 248 },
  { key: "qty", label: "Qty Total", width: 84, num: true },
  { key: "cup", label: "Cup", width: 48, num: true },
  { key: "screen", label: "Screen", width: 52, num: true },
  { key: "prod", label: "Prod", width: 48, num: true },
  { key: "packing", label: "Pack", width: 48, num: true },
  { key: "loading", label: "Load", width: 48, num: true },
  { key: "shipped", label: "Kirim", width: 48, sort: "shipped", num: true },
  { key: "notif_helper", label: "Helper", width: 56, num: true },
  { key: "notif_driver", label: "Driver", width: 56, num: true },
  { key: "notif_kepala", label: "Kepala Produksi", width: 88, num: true },
];

export const MOBILE_DELIVERY_STATUS_COL_KEYS = [
  "cup", "screen", "prod", "packing", "loading", "shipped",
];

export const MOBILE_DELIVERY_NOTIF_COL_KEYS = [
  "notif_helper", "notif_driver", "notif_kepala",
];

export const PWA_SJ_NOTIFICATION_ROLES = {
  notif_helper: { role: "helper", label: "Helper" },
  notif_driver: { role: "driver", label: "Driver" },
  notif_kepala: { role: "head_production", label: "Kepala Produksi" },
};

export function idlePwaSjNotifications() {
  return { helper: "idle", driver: "idle", head_production: "idle" };
}

/** Marks from GET /delivery/schedule sj_notifications only — never packing/production/estimation. */
export function pwaSjNotificationTones(row) {
  const src = row?.sj_notifications && typeof row.sj_notifications === "object"
    ? row.sj_notifications
    : {};
  const tone = (role) => (src[role] === "sent" ? "done" : "idle");
  return {
    helper: tone("helper"),
    driver: tone("driver"),
    head_production: tone("head_production"),
  };
}

/** Exact SJ lines from GET /delivery/schedule — never item_label / PO line_items. */
export function sjItemLinesFromRow(row) {
  const lines = row?.sj_item_lines;
  return Array.isArray(lines) ? lines.filter((ln) => ln && typeof ln === "object") : [];
}

export function groupSjItemLines(lines) {
  const groups = [];
  const index = new Map();
  for (const ln of sjItemLinesFromRow({ sj_item_lines: lines })) {
    const key = `${ln.shipment_id || ""}::${ln.sj_number || ""}`;
    let group = index.get(key);
    if (!group) {
      group = {
        shipment_id: ln.shipment_id || "",
        sj_number: ln.sj_number || "",
        lines: [],
      };
      index.set(key, group);
      groups.push(group);
    }
    group.lines.push(ln);
  }
  return groups;
}

export function sjItemLineKey(line, fallbackIdx = 0) {
  const ship = line?.shipment_id || "";
  if (line?.line_id) return `${ship}:${line.line_id}:${fallbackIdx}`;
  if (line?.line_idx !== undefined && line?.line_idx !== null && line?.line_idx !== "") {
    return `${ship}:idx:${line.line_idx}:${fallbackIdx}`;
  }
  return `${ship}:pos:${fallbackIdx}`;
}

const PRODUCTION_ROLE_STATUS_FIELDS = [
  "helper_required",
  "helper_prepared",
  "screen_qty",
  "current_stage",
  "cancelled",
  "hold",
  "operators",
  "print_completed_qty",
];

function canonicalOrderId(value) {
  if (value == null) return "";
  const id = String(value).trim();
  return id;
}

/** Index Jadwal Produksi rows by canonical order id only. Never name / line_idx / 0. */
export function indexProductionStatusByOrderId(rows) {
  const map = new Map();
  for (const row of rows || []) {
    const id = canonicalOrderId(row?.id);
    if (!id) continue;
    if (!map.has(id)) map.set(id, row);
  }
  return map;
}

/**
 * Attach canonical Cup/Screen/Prod fields from a matching production row.
 * Refuses merge unless both sides share the same order id.
 * Does not copy packing / loading / delivery qty.
 */
export function withProductionRoleStatus(deliveryRow, productionRow) {
  if (!deliveryRow || typeof deliveryRow !== "object") return deliveryRow;
  const deliveryId = canonicalOrderId(deliveryRow.id);
  const productionId = canonicalOrderId(productionRow?.id);
  if (!deliveryId || !productionId || deliveryId !== productionId) return deliveryRow;
  const next = { ...deliveryRow };
  for (const key of PRODUCTION_ROLE_STATUS_FIELDS) {
    if (productionRow[key] !== undefined) next[key] = productionRow[key];
  }
  const deliveryPrint = Number(next.print_qty_total);
  const productionPrint = Number(productionRow.print_qty_total);
  if (!(Number.isFinite(deliveryPrint) && deliveryPrint > 0)
    && Number.isFinite(productionPrint) && productionPrint > 0) {
    next.print_qty_total = productionRow.print_qty_total;
  }
  return next;
}

export function deliveryRowWithProductionStatus(deliveryRow, productionById) {
  const id = canonicalOrderId(deliveryRow?.id);
  if (!id) return deliveryRow;
  const productionRow = productionById instanceof Map
    ? productionById.get(id)
    : productionById?.[id];
  return withProductionRoleStatus(deliveryRow, productionRow);
}

export const ROUTE_OPT_ACTION_PERMS = [
  "route_optimizer.create",
  "route_optimizer.optimize",
  "route_optimizer.manage",
];

export function canOptimizeFromDeliverySchedule(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return ROUTE_OPT_ACTION_PERMS.some((p) => hasPerm(p));
}

export const OPT_STATUS = {
  NONE: "belum_dioptimasi",
  OPTIMIZED: "sudah_dioptimasi",
  STALE: "perlu_optimasi_ulang",
};

export const OPT_STATUS_LABELS = {
  [OPT_STATUS.NONE]: "Belum Dioptimasi",
  [OPT_STATUS.OPTIMIZED]: "Sudah Dioptimasi",
  [OPT_STATUS.STALE]: "Perlu Optimasi Ulang",
};

export function optimizationStatusMeta(row) {
  const code = row?.optimization_status || OPT_STATUS.NONE;
  return {
    code,
    label: row?.optimization_status_label || OPT_STATUS_LABELS[code] || OPT_STATUS_LABELS[OPT_STATUS.NONE],
    routeId: row?.route_id || null,
  };
}

export const MOBILE_SCHEDULE_REVIEW_PATH = "/m/delivery-schedule/review-optimasi";

/** Desktop Jadwal Pengiriman — stays in AppShell (never /m/...). */
export const DESKTOP_DELIVERY_SCHEDULE_PATH = "/produksi/jadwal-pengiriman";
export const DESKTOP_SCHEDULE_REVIEW_PATH = "/produksi/jadwal-pengiriman/review-optimasi";

function scheduleReviewHref(basePath, orderIds) {
  const ids = (orderIds || []).filter(Boolean).map(String);
  const qs = ids.length ? `?ids=${encodeURIComponent(ids.join(","))}` : "";
  return `${basePath}${qs}`;
}

export function mobileScheduleReviewHref(orderIds) {
  return scheduleReviewHref(MOBILE_SCHEDULE_REVIEW_PATH, orderIds);
}

export function desktopScheduleReviewHref(orderIds) {
  return scheduleReviewHref(DESKTOP_SCHEDULE_REVIEW_PATH, orderIds);
}

export function mobileDeliverySheetColumnStyle(col) {
  const w = Number(col?.width) || 80;
  return {
    width: `calc(${w}px * var(--production-sheet-zoom, 1))`,
    minWidth: `calc(${w}px * var(--production-sheet-zoom, 1))`,
  };
}

/** Canonical SJ id from GET /delivery/schedule — shipment.id || sales_delivery_id. */
export function canonicalDeliverySjId(row) {
  return String(row?.shipment_id || "").trim();
}

/**
 * Canonical SJ number from the same schedule row.
 * Matches desktop scheduleRowSjNumber: sj_label → latest_sj_number → sj_numbers[0].
 */
export function canonicalDeliverySjNumber(row) {
  const label = String(row?.sj_label || "").trim();
  if (label) return label;
  const latest = String(row?.latest_sj_number || "").trim();
  if (latest) return latest;
  const first = Array.isArray(row?.sj_numbers) ? String(row.sj_numbers[0] || "").trim() : "";
  return first;
}

/**
 * PWA Jadwal Pengiriman visibility: a current valid Surat Jalan on the schedule row.
 * Does not infer from packing, loading, production, estimation date, or customer name.
 * Cancelled/deleted SJ follows GET /delivery/schedule emission (no invented is_cancelled).
 */
export function isValidPwaDeliverySjRow(row) {
  return Boolean(canonicalDeliverySjId(row) && canonicalDeliverySjNumber(row));
}

export function pwaDeliverySjRowKey(row) {
  const orderId = String(row?.id || "").trim();
  const sjId = canonicalDeliverySjId(row);
  if (orderId && sjId) return `${orderId}:${sjId}`;
  return sjId || orderId || String(row?.order_number || "");
}

/**
 * One canonical SJ → one PWA table row.
 * Drops no-SJ PO rows (including overdue/today/future estimate-only rows).
 * Dedupes by order id + shipment_id. Never groups by customer / item / display text.
 */
export function pwaDeliveryScheduleRows(rows) {
  const out = [];
  const seen = new Set();
  for (const row of rows || []) {
    if (!isValidPwaDeliverySjRow(row)) continue;
    const key = pwaDeliverySjRowKey(row);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    out.push(row);
  }
  return out;
}
