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
  { key: "effective_delivery_date", label: "Tanggal Kirim", width: 88, sort: "effective_delivery_date" },
  { key: "po_number", label: "No PO", width: 92, sort: "po_number" },
  { key: "customer", label: "Customer", width: 120, sort: "customer" },
  { key: "sj", label: "No SJ", width: 96 },
  { key: "packing", label: "Packing", width: 88, num: true },
  { key: "shipped", label: "Terkirim", width: 72, sort: "shipped", num: true },
  { key: "shippable", label: "Dapat Dikirim", width: 80, sort: "shippable", num: true },
  { key: "driver", label: "Driver", width: 88, sort: "driver" },
  { key: "opt_route", label: "Optimasi Rute", width: 110 },
  { key: "status", label: "Status", width: 88, sort: "status" },
  { key: "deadline", label: "Deadline", width: 78, sort: "deadline" },
  { key: "jadwal_manual", label: "Jadwal Manual", width: 78, sort: "jadwal_manual" },
  { key: "actions", label: "Aksi", width: 140 },
];

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

export function mobileScheduleReviewHref(orderIds) {
  const ids = (orderIds || []).filter(Boolean).map(String);
  const qs = ids.length ? `?ids=${encodeURIComponent(ids.join(","))}` : "";
  return `${MOBILE_SCHEDULE_REVIEW_PATH}${qs}`;
}

export function mobileDeliverySheetColumnStyle(col) {
  const w = Number(col?.width) || 80;
  return {
    width: `calc(${w}px * var(--production-sheet-zoom, 1))`,
    minWidth: `calc(${w}px * var(--production-sheet-zoom, 1))`,
  };
}
