import { formatSalesInvoiceDate } from "../../lib/salesInvoiceDate";

export function formatIdDate(iso) {
  return formatSalesInvoiceDate(iso) || "—";
}

export function formatRpJt(amount) {
  if (amount == null || Number.isNaN(amount)) return "—";
  const jt = amount / 1_000_000;
  const rounded = Math.round(jt * 10) / 10;
  const text = Number.isInteger(rounded)
    ? String(rounded)
    : rounded.toFixed(1).replace(".", ",");
  return `Rp${text} jt`;
}

export function formatRpFull(amount) {
  if (amount == null || Number.isNaN(amount)) return "—";
  return `Rp${Math.round(amount).toLocaleString("id-ID")}`;
}

export function formatPcs(n) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${Math.round(n).toLocaleString("id-ID")} pcs`;
}

export function formatPct(n, digits = 1) {
  if (n == null || Number.isNaN(n)) return "—";
  return `${n.toFixed(digits).replace(".", ",")}%`;
}
