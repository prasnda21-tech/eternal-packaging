/** Owner Analytics helpers — access, formatting, drill-down. No KPI formulas. */

import { formatAxisMoney, formatMoney, jakartaDate, periodRange, shouldApplyDashboardResponse } from "./financialDashboard";

export const OWNER_ROLES = new Set(["direktur_utama", "holding_team"]);
export const OWNER_PERM = "dashboard.owner.view";
export const OWNER_NAV = "nav.dashboard.owner";
export const OWNER_PATH = "/dashboard/owner";

export { formatAxisMoney, formatMoney, jakartaDate, periodRange, shouldApplyDashboardResponse };

export function canAccessOwnerDashboard(user, hasPerm) {
  const role = String(user?.role || "").trim().toLowerCase();
  if (!OWNER_ROLES.has(role)) return false;
  if (typeof hasPerm === "function") return !!hasPerm(OWNER_PERM) || !!hasPerm(OWNER_NAV);
  const perms = user?.permissions || [];
  return perms.includes(OWNER_PERM) || perms.includes(OWNER_NAV);
}

export function ownerPresetFromSearch(search) {
  const params = new URLSearchParams(search || "");
  const preset = params.get("preset");
  if (["today", "month", "year", "custom"].includes(preset)) return preset;
  return "month";
}

export function ownerRangeFromSearch(search, now = new Date()) {
  const params = new URLSearchParams(search || "");
  const preset = ownerPresetFromSearch(search);
  if (preset === "custom") {
    const today = jakartaDate(now);
    return {
      preset,
      start: params.get("start") || today,
      end: params.get("end") || today,
    };
  }
  const ranged = periodRange(preset, {}, now);
  return { preset, start: ranged.start, end: ranged.end };
}

export function formatOwnerStamp(iso) {
  if (!iso) return "—";
  try {
    return new Intl.DateTimeFormat("id-ID", {
      timeZone: "Asia/Jakarta",
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    }).format(new Date(iso));
  } catch {
    return String(iso);
  }
}

export function moneyOrState(section, { emptyText = "Belum ada data" } = {}) {
  if (!section) return { kind: "unavailable", text: "Data Belum Siap" };
  const status = section.status;
  if (status === "loading") return { kind: "loading", text: "Memuat…" };
  if (status === "error") return { kind: "error", text: "Tidak dapat memuat data" };
  if (status === "forbidden") return { kind: "error", text: "Tidak ada izin" };
  if (status === "not_ready") return { kind: "not_ready", text: "Data Belum Siap" };
  if (status === "empty") return { kind: "empty", text: emptyText };
  if (section.amount == null && section.count == null) {
    return { kind: "unavailable", text: "Data Belum Lengkap" };
  }
  if (section.amount != null) return { kind: "value", text: formatMoney(section.amount) };
  if (section.count != null) return { kind: "value", text: String(section.count) };
  return { kind: "unavailable", text: "Data Belum Lengkap" };
}

export function countOrState(section, { emptyText = "0" } = {}) {
  if (!section) return { kind: "unavailable", text: "—" };
  const status = section.status;
  if (status === "loading") return { kind: "loading", text: "…" };
  if (status === "error") return { kind: "error", text: "—" };
  if (status === "not_ready") return { kind: "not_ready", text: "Data Belum Siap" };
  const count = section.count;
  if (count == null) return { kind: "empty", text: emptyText };
  return { kind: "value", text: String(count) };
}

export function mapOwnerTrend(trend) {
  if (!trend) return { status: "unavailable", points: [] };
  if (trend.status === "error") return { status: "error", points: [], detail: trend.detail };
  if (trend.status === "empty") return { status: "empty", points: [], detail: trend.detail };
  const points = Array.isArray(trend.points)
    ? trend.points.map((p) => ({ label: p.label, amount: p.omzet ?? p.amount }))
    : [];
  return {
    status: trend.status || "complete",
    points,
    grain: trend.grain_label || trend.grain || "",
    detail: trend.detail,
  };
}
