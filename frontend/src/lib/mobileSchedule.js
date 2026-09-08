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

export function mobileScheduleHref(orderId) {
  if (!orderId) return "/m/orders";
  return `/m/orders/${encodeURIComponent(orderId)}`;
}

export const MOBILE_SCHEDULE_ZOOM_LEVELS = [50, 60, 70, 80, 90, 100, 110, 125, 150];
export const MOBILE_SCHEDULE_ZOOM_DEFAULT = 100;
export const MOBILE_SCHEDULE_ZOOM_MIN = 50;
export const MOBILE_SCHEDULE_ZOOM_MAX = 150;
export const MOBILE_SCHEDULE_ZOOM_FIT = 70;

export function clampMobileScheduleZoom(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return MOBILE_SCHEDULE_ZOOM_DEFAULT;
  if (n <= MOBILE_SCHEDULE_ZOOM_MIN) return MOBILE_SCHEDULE_ZOOM_MIN;
  if (n >= MOBILE_SCHEDULE_ZOOM_MAX) return MOBILE_SCHEDULE_ZOOM_MAX;
  return MOBILE_SCHEDULE_ZOOM_LEVELS.reduce((best, level) => (
    Math.abs(level - n) < Math.abs(best - n) ? level : best
  ));
}

export function stepMobileScheduleZoom(current, direction) {
  const levels = MOBILE_SCHEDULE_ZOOM_LEVELS;
  const cur = clampMobileScheduleZoom(current);
  const idx = Math.max(0, levels.indexOf(cur));
  const nextIdx = Math.min(levels.length - 1, Math.max(0, idx + Number(direction || 0)));
  return levels[nextIdx];
}

export function fitMobileScheduleZoom() {
  return MOBILE_SCHEDULE_ZOOM_FIT;
}

export function mobileScheduleZoomFactor(percent) {
  return clampMobileScheduleZoom(percent) / 100;
}
