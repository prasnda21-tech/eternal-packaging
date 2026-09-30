export function salesApiError(err, fallback) {
  const detail = err?.response?.data?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  return fallback;
}

export function salesInvoiceMonth(value) {
  const match = /^(\d{4}-\d{2})/.exec(String(value || ""));
  return match ? match[1] : "";
}

export function paymentStatusLabel(status) {
  const key = String(status || "").trim().toUpperCase();
  if (key === "LUNAS") return "Lunas";
  if (key === "DP") return "DP";
  if (key === "BELUM DIBAYAR") return "Belum dibayar";
  return key || "—";
}

export function paymentStatusTone(status) {
  const key = String(status || "").trim().toUpperCase();
  if (key === "LUNAS") return "is-done";
  if (key === "DP") return "is-partial";
  if (key === "BELUM DIBAYAR") return "is-hold";
  return "";
}

export function receivableById(rows) {
  const map = new Map();
  for (const row of rows || []) {
    if (row && row.id) map.set(String(row.id), row);
  }
  return map;
}

export function invoiceOutstandingAmount(invoice, receivable) {
  if (receivable && Number.isFinite(Number(receivable.outstanding_amount))) {
    return Number(receivable.outstanding_amount);
  }
  if (String(invoice?.payment_status || "").toUpperCase() === "LUNAS") return 0;
  return null;
}

export function salesInvoiceHref(row, from, monthOverride) {
  const params = new URLSearchParams();
  const month = salesInvoiceMonth(monthOverride) || salesInvoiceMonth(row?.invoice_date);
  if (month) params.set("month", month);
  if (from) params.set("from", from);
  const query = params.toString();
  return `/sales/invoices/${encodeURIComponent(row?.id || "")}${query ? `?${query}` : ""}`;
}

export function mergeSalesInvoiceDetail(invoice, receivable) {
  const source = invoice || receivable;
  if (!source) return null;
  const grand = Number(invoice?.grand_total ?? receivable?.grand_total);
  const outstanding = invoiceOutstandingAmount(invoice, receivable);
  let paid = null;
  if (receivable && Number.isFinite(Number(receivable.paid_amount))) paid = Number(receivable.paid_amount);
  else if (outstanding === 0 && Number.isFinite(grand)) paid = grand;
  let paymentStatus = String(invoice?.payment_status || "").trim();
  if (!paymentStatus && outstanding != null && Number.isFinite(grand)) {
    if (outstanding <= 0) paymentStatus = "LUNAS";
    else if (paid > 0) paymentStatus = "DP";
    else paymentStatus = "BELUM DIBAYAR";
  }
  return {
    id: source.id,
    invoice_number: source.invoice_number || "",
    customer_name: source.customer_name || "",
    invoice_date: source.invoice_date || "",
    due_date: receivable?.due_date || "",
    grand_total: Number.isFinite(grand) ? grand : null,
    payment_status: paymentStatus,
    paid_amount: paid,
    outstanding_amount: outstanding,
  };
}
