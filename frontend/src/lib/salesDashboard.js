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

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export function positionLabel(asOf) {
  const text = String(asOf || "").trim();
  return ISO_DAY.test(text) ? `Posisi saat ini · ${text}` : "Posisi saat ini";
}

export function balanceAsOfLabel(endDate) {
  const text = String(endDate || "").trim();
  return ISO_DAY.test(text) ? `Per ${text}` : "Per tanggal akhir periode";
}

export function plBars(performance) {
  if (!performance) return null;
  const status = performance.status;
  if (!status || status === "error" || status === "empty" || status === "unavailable" || status === "loading") return null;
  const fields = [
    ["revenue", "Pendapatan"],
    ["cogs", "HPP"],
    ["operating_expense", "Beban operasional"],
  ];
  const bars = [];
  for (const [key, label] of fields) {
    if (performance[key] == null) return null;
    const amount = Number(performance[key]);
    if (!Number.isFinite(amount)) return null;
    bars.push({ key, label, amount });
  }
  return bars;
}

export function otherResultLines(performance) {
  if (!performance) return [];
  const status = performance.status;
  if (!status || status === "error" || status === "empty" || status === "unavailable" || status === "loading") return [];
  const lines = [];
  for (const [key, label] of [["other_income", "Pendapatan lain"], ["other_expense", "Beban lain"]]) {
    if (performance[key] == null) continue;
    const amount = Number(performance[key]);
    if (!Number.isFinite(amount) || amount === 0) continue;
    lines.push({ key, label, amount });
  }
  return lines;
}
