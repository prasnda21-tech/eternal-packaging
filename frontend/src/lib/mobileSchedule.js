import { NAV_KEYS } from "./navAccess";
import { FILTER_DEFAULTS } from "./productionScheduleFilters";
import { productionDashboardRequestParams } from "./productionScheduleTable";

export const MOBILE_SCHEDULE_VIEW_PERMS = ["schedule.view", NAV_KEYS.schedule];

export function canViewMobileSchedule(hasPerm) {
  if (typeof hasPerm !== "function") return false;
  return MOBILE_SCHEDULE_VIEW_PERMS.some((perm) => hasPerm(perm));
}

export function rowsFromProductionDashboard(payload) {
  if (Array.isArray(payload?.rows)) return payload.rows;
  return [];
}

export function defaultProductionDashboardParams() {
  return productionDashboardRequestParams(FILTER_DEFAULTS);
}

export function mobileProductionDashboardParams(filters = {}) {
  return {
    ...productionDashboardRequestParams(filters),
    view: "summary",
  };
}

export function mobileScheduleHref(orderId) {
  if (!orderId) return "/m/orders";
  return `/m/orders/${encodeURIComponent(orderId)}`;
}
