/** Desktop sidebar expanded/collapsed preference. Presentation only — not authz. */

import { SIDEBAR_EXPANDED_WIDTH, SIDEBAR_RAIL_WIDTH } from "./sidebarPresentation";

export const SIDEBAR_MODE_EXPANDED = "expanded";
export const SIDEBAR_MODE_COLLAPSED = "collapsed";
export { SIDEBAR_EXPANDED_WIDTH };
export const SIDEBAR_COMPACT_BREAKPOINT = 1366;
export const SIDEBAR_MODE_STORAGE_PREFIX = "ep.ui.sidebarMode.v1";

export function sidebarModeStorageKey(userId) {
  const id = String(userId || "").trim();
  return id ? `${SIDEBAR_MODE_STORAGE_PREFIX}.${id}` : `${SIDEBAR_MODE_STORAGE_PREFIX}.anon`;
}

export function normalizeSidebarMode(value) {
  if (value === SIDEBAR_MODE_EXPANDED || value === SIDEBAR_MODE_COLLAPSED) return value;
  return null;
}

export function defaultSidebarModeForWidth(width) {
  const w = Number(width);
  if (!Number.isFinite(w)) return SIDEBAR_MODE_COLLAPSED;
  return w >= SIDEBAR_COMPACT_BREAKPOINT ? SIDEBAR_MODE_EXPANDED : SIDEBAR_MODE_COLLAPSED;
}

export function sidebarRailWidth(mode) {
  return normalizeSidebarMode(mode) === SIDEBAR_MODE_EXPANDED
    ? SIDEBAR_EXPANDED_WIDTH
    : SIDEBAR_RAIL_WIDTH;
}

export function readSidebarMode(storage, userId) {
  try {
    const raw = storage?.getItem?.(sidebarModeStorageKey(userId));
    if (!raw) return null;
    return normalizeSidebarMode(raw);
  } catch (_) {
    return null;
  }
}

export function writeSidebarMode(storage, userId, mode) {
  const next = normalizeSidebarMode(mode);
  if (!next) return null;
  try {
    storage?.setItem?.(sidebarModeStorageKey(userId), next);
  } catch (_) {
    /* ignore quota / private mode */
  }
  return next;
}

/** Prefer stored user choice; otherwise viewport default. Never invent other values. */
export function resolveSidebarMode({ storage, userId, viewportWidth } = {}) {
  const stored = readSidebarMode(storage, userId);
  if (stored) {
    return { mode: stored, userSet: true };
  }
  return {
    mode: defaultSidebarModeForWidth(viewportWidth),
    userSet: false,
  };
}

export function toggleSidebarMode(mode) {
  return normalizeSidebarMode(mode) === SIDEBAR_MODE_EXPANDED
    ? SIDEBAR_MODE_COLLAPSED
    : SIDEBAR_MODE_EXPANDED;
}
