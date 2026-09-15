/** Session persistence for PWA ops tables (filters + scroll). Zoom uses productionSheetZoom.
 * Active Role uses user-scoped localStorage (survives PWA navigation until logout).
 */

function store() {
  try {
    return typeof sessionStorage !== "undefined" ? sessionStorage : null;
  } catch {
    return null;
  }
}

function localStore() {
  try {
    return typeof localStorage !== "undefined" ? localStorage : null;
  } catch {
    return null;
  }
}

export const MOBILE_PROD_FILTER_KEY = "eternal.ui.mobile-prod-schedule-filters";
export const MOBILE_DELIVERY_FILTER_KEY = "eternal.ui.mobile-delivery-schedule-filters";
export const MOBILE_PROD_SCROLL_KEY = "eternal.ui.mobile-prod-schedule-scroll";
export const MOBILE_DELIVERY_SCROLL_KEY = "eternal.ui.mobile-delivery-schedule-scroll";
export const MOBILE_ACTIVE_ROLE_KEY = "eternal.ui.mobile-active-role";

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

const LOCAL_OPS_KEY_PREFIXES = [
  MOBILE_ACTIVE_ROLE_KEY,
];

export function readActiveRoleId(userId) {
  const s = localStore();
  if (!s) return "";
  try {
    const raw = s.getItem(scopedOpsKey(MOBILE_ACTIVE_ROLE_KEY, userId));
    if (raw == null || raw === "") return "";
    return String(raw).trim();
  } catch {
    return "";
  }
}

export function writeActiveRoleId(userId, roleId) {
  const s = localStore();
  if (!s) return;
  const key = scopedOpsKey(MOBILE_ACTIVE_ROLE_KEY, userId);
  const id = String(roleId || "").trim();
  try {
    if (!id) s.removeItem(key);
    else s.setItem(key, id);
  } catch {
    /* quota */
  }
}

function clearPrefixedKeys(storage, prefixes, userId) {
  if (!storage) return;
  const uid = userId != null ? String(userId) : "";
  const toRemove = [];
  try {
    for (let i = 0; i < storage.length; i += 1) {
      const k = storage.key(i);
      if (!k) continue;
      for (const base of prefixes) {
        if (k === base || (uid && k === `${base}.${uid}`)) toRemove.push(k);
      }
    }
    prefixes.forEach((base) => {
      if (!toRemove.includes(base)) toRemove.push(base);
      if (uid) {
        const scoped = `${base}.${uid}`;
        if (!toRemove.includes(scoped)) toRemove.push(scoped);
      }
    });
    toRemove.forEach((k) => {
      try { storage.removeItem(k); } catch { /* ignore */ }
    });
  } catch {
    /* ignore */
  }
}

/** Remove scoped + legacy unscoped ops session keys for a user. */
export function clearMobileOpsSessionForUser(userId) {
  clearPrefixedKeys(store(), OPS_KEY_PREFIXES, userId);
  clearPrefixedKeys(localStore(), LOCAL_OPS_KEY_PREFIXES, userId);
}
