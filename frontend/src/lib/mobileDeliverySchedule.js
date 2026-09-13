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

/** Dense spreadsheet columns — Web semantics + packing progress + queue badge. */
export const MOBILE_DELIVERY_SCHEDULE_COLS = [
  { key: "prio", label: "Prioritas", width: 92 },
  { key: "effective_delivery_date", label: "Tanggal Pengiriman", width: 98, sort: "effective_delivery_date" },
  { key: "po_number", label: "No PO", width: 92, sort: "po_number" },
  { key: "customer", label: "Customer", width: 148, sort: "customer" },
  { key: "item_line", label: "Item Line", width: 156 },
  { key: "sj", label: "No SJ", width: 110 },
  { key: "packing", label: "Packing", width: 88, num: true },
  { key: "shipped", label: "Terkirim", width: 72, sort: "shipped", num: true },
  { key: "shippable", label: "Dapat Dikirim", width: 80, sort: "shippable", num: true },
  { key: "opt_route", label: "Optimasi Rute", width: 110 },
];

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
