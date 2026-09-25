const MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"];

export function formatInvoiceDate(value) {
  const raw = String(value || "").trim().slice(0, 10);
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(raw);
  if (!match) return raw;
  const month = MONTHS[Number(match[2]) - 1];
  if (!month) return raw;
  return `${match[3]} ${month} ${match[1]}`;
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
