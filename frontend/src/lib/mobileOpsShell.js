/** Prefer mobile operational shell for shared routes (e.g. Optimasi Rute) inside PWA. */

import { isStandaloneDisplay } from "./mobilePwa";

export const MOBILE_OPS_SHELL_KEY = "ep.mobileOpsShell";

export function markMobileOpsShell(storage = typeof sessionStorage !== "undefined" ? sessionStorage : null) {
  try {
    storage?.setItem(MOBILE_OPS_SHELL_KEY, "1");
  } catch {
    /* ignore quota / private mode */
  }
}

export function preferMobileOpsShell(
  win = typeof window !== "undefined" ? window : null,
  storage = typeof sessionStorage !== "undefined" ? sessionStorage : null,
) {
  if (isStandaloneDisplay(win)) return true;
  try {
    return storage?.getItem(MOBILE_OPS_SHELL_KEY) === "1";
  } catch {
    return false;
  }
}

/** Same-window in-app path for the shared Optimasi Rute editor (never absolute / external). */
export function optimasiRutePath(routeId) {
  const id = String(routeId || "").trim();
  if (!id) return "/optimasi-rute";
  return `/optimasi-rute/${encodeURIComponent(id)}`;
}

/**
 * Navigate to Optimasi Rute inside the current window (PWA-safe).
 * Must not use window.open, target=_blank, or absolute production URLs.
 */
export function navigateToOptimasiRute(navigate, routeId, { from } = {}) {
  if (typeof navigate !== "function") return;
  const to = optimasiRutePath(routeId);
  if (from) navigate(to, { state: { from } });
  else navigate(to);
}
