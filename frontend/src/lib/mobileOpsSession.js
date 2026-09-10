/** Session persistence for PWA ops tables (filters + scroll). Zoom uses productionSheetZoom. */

function store() {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}

export const MOBILE_PROD_FILTER_KEY = "eternal.ui.mobile-prod-schedule-filters";
export const MOBILE_DELIVERY_FILTER_KEY = "eternal.ui.mobile-delivery-schedule-filters";
export const MOBILE_PROD_SCROLL_KEY = "eternal.ui.mobile-prod-schedule-scroll";
export const MOBILE_DELIVERY_SCROLL_KEY = "eternal.ui.mobile-delivery-schedule-scroll";

export function readJsonSession(key, fallback) {
  const s = store();
  if (!s) return fallback;
  try {
    const raw = s.getItem(key);
    if (raw == null || raw === "") return fallback;
    return { ...fallback, ...JSON.parse(raw) };
  } catch {
    return fallback;
  }
}

export function writeJsonSession(key, value) {
  const s = store();
  if (!s) return;
  try {
    s.setItem(key, JSON.stringify(value));
  } catch {
    /* quota */
  }
}

export function readScrollSession(key) {
  const s = store();
  if (!s) return null;
  try {
    const raw = s.getItem(key);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return {
      left: Number(parsed?.left) || 0,
      top: Number(parsed?.top) || 0,
    };
  } catch {
    return null;
  }
}

export function writeScrollSession(key, left, top) {
  const s = store();
  if (!s) return;
  try {
    s.setItem(key, JSON.stringify({ left: Number(left) || 0, top: Number(top) || 0 }));
  } catch {
    /* quota */
  }
}

export function clearSessionKey(key) {
  const s = store();
  if (!s) return;
  try {
    s.removeItem(key);
  } catch {
    /* ignore */
  }
}
