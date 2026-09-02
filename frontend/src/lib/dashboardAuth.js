import { formatApiError } from "./errors";

/** Backend `current_user` 401 when no cookie/token is present. */
export const DASHBOARD_LOGIN_REQUIRED = "Silakan masuk terlebih dahulu";
export const DASHBOARD_SESSION_EXPIRED = "Sesi berakhir, silakan masuk kembali";
export const DASHBOARD_LOAD_FAILED = "Gagal memuat dashboard";

function isLoginChallengeCopy(text) {
  const t = String(text || "").trim();
  return t === DASHBOARD_LOGIN_REQUIRED || t === DASHBOARD_SESSION_EXPIRED;
}

/**
 * Page-level view for /dashboard (HoPDashboard).
 * Auth context (user + loading) is authoritative — the same source AppShell uses.
 */
export function dashboardPageView({ loading, user, data, err }) {
  if (loading) return "loading";
  if (!user) return "unauthenticated";
  if (err) return "error";
  if (!data) return "loading";
  return "ready";
}

/**
 * Map /dashboard/summary (or related) fetch failures to page copy.
 *
 * Authenticated session stays authenticated even if this API call returns 401.
 * 403 stays a permission problem. Other failures stay data errors.
 * Never emit login-required copy while `user` is present, and never emit it
 * while auth is still loading.
 */
export function dashboardFetchErrorMessage(err, { user, loading } = {}) {
  if (loading) return null;
  if (!user) return null;

  const status = err?.response?.status;
  const raw = formatApiError(err, DASHBOARD_LOAD_FAILED);

  if (status === 401 || isLoginChallengeCopy(raw)) {
    return DASHBOARD_LOAD_FAILED;
  }
  if (status === 403) {
    return raw || DASHBOARD_LOAD_FAILED;
  }
  return raw || DASHBOARD_LOAD_FAILED;
}
