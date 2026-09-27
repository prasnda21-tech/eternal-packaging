/** Presentation helpers for Dashboard Penjualan. Formulas stay on the server. */

import { formatMoney } from "./financialDashboard";

export function comparisonDisplay(comparison) {
  if (!comparison || comparison.status === "error" || comparison.available !== true || comparison.percent == null || comparison.percent === "") {
    return { text: "—", arrow: null };
  }
  const ratio = Number(comparison.percent);
  if (!Number.isFinite(ratio)) return { text: "—", arrow: null };
  const text = `${(ratio * 100).toLocaleString("id-ID", { maximumFractionDigits: 1, minimumFractionDigits: 0 })}%`;
  if (comparison.direction === "up") return { text, arrow: "up" };
  if (comparison.direction === "down") return { text, arrow: "down" };
  return { text, arrow: null };
}

export function displayAmount(status, amount, detail) {
  if (status === "error") return "Gagal muat";
  if (status === "loading") return "Memuat…";
  if (status === "empty" || status === "unavailable") return detail || "Belum tersedia";
  if (amount == null || !Number.isFinite(Number(amount))) return "—";
  return formatMoney(amount);
}

export function displayCount(status, count, detail) {
  if (status === "error") return "Gagal muat";
  if (status === "loading") return "Memuat…";
  if (status === "empty" || status === "unavailable") return detail || "Belum tersedia";
  if (count == null || !Number.isFinite(Number(count))) return "—";
  return Number(count).toLocaleString("id-ID");
}

export function formatRatio(ratio) {
  if (ratio == null || ratio === "") return "—";
  const value = Number(ratio);
  if (!Number.isFinite(value)) return "—";
  return `${(value * 100).toLocaleString("id-ID", { maximumFractionDigits: 1, minimumFractionDigits: 0 })}%`;
}
