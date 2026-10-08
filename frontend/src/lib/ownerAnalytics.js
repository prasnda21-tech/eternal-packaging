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

const MONTHS_SHORT_ID = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

/** Presentation-only: API grain → Indonesian label. Does not change API enums. */
export function ownerCashGrainLabel(grain) {
  const raw = String(grain || "").trim().toLowerCase();
  if (raw === "day" || raw === "harian") return "Harian";
  if (raw === "month" || raw === "bulanan") return "Bulanan";
  if (!raw) return "";
  return String(grain);
}

/** Axis tick: daily `01 Okt`; monthly `Okt` or `Okt 2026` when showYear. */
export function formatCashAxisLabel(period, { showYear = false } = {}) {
  const p = String(period || "").trim();
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(p);
  if (day) {
    return `${day[3]} ${MONTHS_SHORT_ID[Number(day[2]) - 1]}`;
  }
  const month = /^(\d{4})-(\d{2})$/.exec(p);
  if (month) {
    const label = MONTHS_SHORT_ID[Number(month[2]) - 1];
    return showYear ? `${label} ${month[1]}` : label;
  }
  return p;
}

/** Tooltip date: full calendar label, not truncated axis form. */
export function formatCashTooltipLabel(period) {
  const p = String(period || "").trim();
  const day = /^(\d{4})-(\d{2})-(\d{2})$/.exec(p);
  if (day) {
    return `${day[3]} ${MONTHS_SHORT_ID[Number(day[2]) - 1]} ${day[1]}`;
  }
  const month = /^(\d{4})-(\d{2})$/.exec(p);
  if (month) {
    return `${MONTHS_SHORT_ID[Number(month[2]) - 1]} ${month[1]}`;
  }
  return p;
}

/** Thin x-axis tick indexes; always keeps first and last. Data points unchanged. */
export function pickCashAxisTickIndexes(count, approxWidthPx = 640) {
  const n = Number(count) || 0;
  if (n <= 0) return [];
  if (n === 1) return [0];
  const width = Number(approxWidthPx) || 640;
  const maxLabels = width < 420 ? 5 : width < 700 ? 7 : 9;
  if (n <= maxLabels) {
    return Array.from({ length: n }, (_, index) => index);
  }
  const indexes = new Set([0, n - 1]);
  const inner = Math.max(1, maxLabels - 2);
  for (let step = 1; step <= inner; step += 1) {
    indexes.add(Math.round((step * (n - 1)) / (inner + 1)));
  }
  return [...indexes].sort((a, b) => a - b);
}

/** Dual-series cash trend for Owner Keuangan (not P&L / omzet). */
export function mapOwnerCashTrend(trend) {
  if (!trend) return { status: "unavailable", points: [], grain: "", grainKey: "" };
  const grainKey = trend.grain || "";
  const grain = ownerCashGrainLabel(grainKey);
  if (trend.status === "error") {
    return { status: "error", points: [], grain, grainKey, detail: trend.detail };
  }
  if (trend.status === "empty") {
    return { status: "empty", points: [], grain, grainKey, detail: trend.detail };
  }
  if (trend.status === "unavailable") {
    return { status: "unavailable", points: [], grain, grainKey, detail: trend.detail };
  }
  if (trend.status === "loading") {
    return { status: "loading", points: [], grain, grainKey, detail: trend.detail };
  }
  const periods = Array.isArray(trend.points) ? trend.points.map((p) => p.period || p.label || "") : [];
  const years = new Set(
    periods
      .map((period) => {
        const m = /^(\d{4})/.exec(String(period || ""));
        return m ? m[1] : null;
      })
      .filter(Boolean),
  );
  const showYear = grainKey === "month" && years.size > 1;
  const points = Array.isArray(trend.points)
    ? trend.points.map((p) => {
      const period = p.period || p.label || "";
      return {
        label: formatCashAxisLabel(period, { showYear }),
        fullLabel: formatCashTooltipLabel(period),
        period,
        cash_in: Number(p.cash_in) || 0,
        cash_out: Number(p.cash_out) || 0,
      };
    })
    : [];
  return {
    status: trend.status || "complete",
    points,
    grain,
    grainKey,
    detail: trend.detail,
  };
}

export function reconOrState(section) {
  if (!section) return { kind: "unavailable", text: "Belum Lengkap", unmatched: null, review: null };
  const status = section.status;
  if (status === "loading") {
    return { kind: "loading", text: "Memuat…", unmatched: null, review: null };
  }
  if (status === "error") {
    return {
      kind: "error",
      text: section.status_label || "Belum Lengkap",
      unmatched: null,
      review: null,
      detail: section.detail,
    };
  }
  const label = section.status_label || (status === "complete" ? "Lengkap" : "Belum Lengkap");
  const unmatched = section.unmatched_count;
  const review = section.review_count;
  return {
    kind: status === "complete" ? "value" : (status === "incomplete" ? "incomplete" : "unavailable"),
    text: label,
    unmatched: unmatched == null ? null : Number(unmatched),
    review: review == null ? null : Number(review),
    detail: section.completeness_reason || section.detail,
  };
}
