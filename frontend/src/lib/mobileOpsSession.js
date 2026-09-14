/** Session persistence for PWA ops tables (filters + scroll). Zoom uses productionSheetZoom. */

import { clearAllMobileTaskListRetention } from "./mobileTaskListRetention";

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

/** User-scope legacy ops keys so logout / user switch cannot leak view state. */
export function scopedOpsKey(baseKey, userId) {
  const base = String(baseKey || "");
  const uid = userId != null && String(userId).trim() !== "" ? String(userId) : "";
  if (!uid) return base;
  return `${base}.${uid}`;
}

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

const OPS_KEY_PREFIXES = [
  MOBILE_PROD_FILTER_KEY,
  MOBILE_DELIVERY_FILTER_KEY,
  MOBILE_PROD_SCROLL_KEY,
  MOBILE_DELIVERY_SCROLL_KEY,
];

/** Remove scoped + legacy unscoped ops session keys for a user. */
export function clearMobileOpsSessionForUser(userId) {
  clearAllMobileTaskListRetention();
  const s = store();
  if (!s) return;
  const uid = userId != null ? String(userId) : "";
  const toRemove = [];
  try {
    for (let i = 0; i < s.length; i += 1) {
      const k = s.key(i);
      if (!k) continue;
      for (const base of OPS_KEY_PREFIXES) {
        if (k === base || (uid && k === `${base}.${uid}`) || k.startsWith(`${base}.`)) {
          // Only clear this user's scoped key, or legacy unscoped when uid provided/cleared broadly
          if (k === base || (uid && k === `${base}.${uid}`)) toRemove.push(k);
        }
      }
    }
    // Also clear legacy unscoped keys on any logout (prevent cross-user leak via shared key)
    OPS_KEY_PREFIXES.forEach((base) => {
      if (!toRemove.includes(base)) toRemove.push(base);
      if (uid) {
        const scoped = `${base}.${uid}`;
        if (!toRemove.includes(scoped)) toRemove.push(scoped);
      }
    });
    toRemove.forEach((k) => {
      try { s.removeItem(k); } catch { /* ignore */ }
    });
  } catch {
    /* ignore */
  }
}
