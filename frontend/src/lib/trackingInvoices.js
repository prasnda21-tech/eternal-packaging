import { formatSalesInvoiceDate } from "./salesInvoiceDate";

export function formatInvoiceDate(value) {
  return formatSalesInvoiceDate(value);
}

export function formatInvoiceMoney(value) {
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  const digits = Number.isInteger(amount) ? 0 : 2;
  return `Rp ${amount.toLocaleString("id-ID", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

export function invoiceStatusClass(label) {
  if (label === "Lunas") return "is-paid";
  if (label === "Sebagian") return "is-partial";
  if (label === "Batal") return "is-void";
  return "is-open";
}

export function safeInvoicePdfPath(pdfUrl) {
  const path = String(pdfUrl || "");
  if (!path.startsWith("/api/public/tracking/")) return "";
  if (!path.includes("/invoices/") || !path.includes("/pdf")) return "";
  if (path.includes("://")) return "";
  return path;
}
