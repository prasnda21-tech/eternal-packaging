/** Presentation helpers for Dashboard Rincian (UI only). */

import { formatSalesInvoiceDate } from "./salesInvoiceDate";

const CURRENCY_KEYS = new Set([
  "grand_total", "debit", "credit", "running_balance", "amount",
]);
const QTY_KEYS = new Set(["qty_remaining", "qty"]);
const STATUS_KEYS = new Set([
  "status_label", "status", "financial_status", "payment_status",
  "priority", "priority_label", "delivery_schedule_status", "source_type", "group",
]);
const ID_KEYS = new Set(["po_number", "invoice_number", "number", "account_code", "surat_jalan"]);

/** Contextual KPI label for the primary count/metric card. */
export const KPI_PRIMARY_LABEL = {
  po_aktif: "Total PO Aktif",
  po_urgent: "Total PO Urgent",
  po_hold: "Total PO HOLD",
  deadline_6h: "Deadline ≤ 6 Jam",
  deadline_24h: "Deadline ≤ 24 Jam",
  approval_design: "Menunggu Approval Design",
  approval_payment: "Menunggu Approval Payment",
  perlu_produksi: "Total PO Perlu Produksi",
  sisa_qty: "Total PO Sisa Qty",
  prioritas: "Total PO Prioritas",
  delivery_qty_today: "Total Qty Rencana",
  delivery_qty_overdue: "Total Qty Rencana",
  omzet: "Total Omzet",
  today: "Total Penjualan Hari Ini",
  settlement: "Total Settlement",
  trend: "Total Omzet Tren",
  shipToday: "Pengiriman Hari Ini",
  shipLate: "Pengiriman Terlambat",
  sales: "Total Penjualan",
  purchases: "Total Faktur Pembelian",
  cash: "Total Mutasi",
  profit: "Total Baris Laba/Rugi",
  expense: "Total Beban",
};

export function presentationKind(col = {}) {
  const key = String(col.key || "");
  const type = String(col.type || "text").toLowerCase();
  if (type === "date" || type === "datetime" || type === "currency" || type === "quantity" || type === "status") {
    return type;
  }
  if (type === "number") {
    if (CURRENCY_KEYS.has(key)) return "currency";
    if (QTY_KEYS.has(key)) return "quantity";
    return "number";
  }
  if (STATUS_KEYS.has(key)) return "status";
  if (key.endsWith("_date") || key === "deadline" || key === "tanggal_pengiriman" || key === "tanggal_kirim" || key === "date") {
    return "date";
  }
  return type || "text";
}

export function isIdentityKey(key) {
  return ID_KEYS.has(String(key || ""));
}

/** `2026-10-01` → `01/10/2026`. Leaves non-ISO text unchanged. */
export function formatIdDate(value) {
  if (value == null || value === "") return "—";
  const text = String(value).trim();
  const day = text.slice(0, 10);
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(day);
  if (!m) return text;
  return `${m[3]}/${m[2]}/${m[1]}`;
}

/** ISO datetime → `01/10/2026 14:30 WIB` when parseable. */
export function formatIdDateTime(value) {
  if (value == null || value === "") return "—";
  const text = String(value).trim();
  const d = new Date(text);
  if (Number.isNaN(d.getTime())) {
    if (/^\d{4}-\d{2}-\d{2}/.test(text)) return `${formatIdDate(text)} WIB`;
    return text;
  }
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Jakarta",
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).formatToParts(d);
  const get = (type) => parts.find((p) => p.type === type)?.value || "";
  return `${get("day")}/${get("month")}/${get("year")} ${get("hour")}:${get("minute")} WIB`;
}

/** `7500` → `Rp7.500`. Null/blank → em dash. Zero stays `Rp0`. */
export function formatIdRupiah(value) {
  if (value == null || value === "") return "—";
  const amount = Number(value);
  if (!Number.isFinite(amount)) return "—";
  const abs = Math.abs(amount).toLocaleString("id-ID", { maximumFractionDigits: 2 });
  return amount < 0 ? `-Rp${abs}` : `Rp${abs}`;
}

/** `12500` → `12.500`. Null/blank → em dash. Zero stays `0`. */
export function formatIdQty(value, unit = "") {
  if (value == null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return "—";
  const body = num.toLocaleString("id-ID", { maximumFractionDigits: 3 });
  const u = String(unit || "").trim();
  return u ? `${body} ${u}` : body;
}

export function formatIdNumber(value) {
  if (value == null || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  return num.toLocaleString("id-ID", { maximumFractionDigits: 3 });
}

export function formatCellValue(kind, raw, { unit = "" } = {}) {
  switch (kind) {
    case "date":
      return formatIdDate(raw);
    case "datetime":
      return formatIdDateTime(raw);
    case "currency":
      return formatIdRupiah(raw);
    case "quantity":
      return formatIdQty(raw, unit);
    case "number":
      return formatIdNumber(raw);
    case "status":
    case "text":
    default:
      if (raw == null || raw === "") return "—";
      return String(raw);
  }
}

/** Sales Dashboard range only. Storage and URLs stay YYYY-MM-DD. */
export function formatSalesDashboardRange(start, end) {
  const a = formatSalesInvoiceDate(start);
  const b = formatSalesInvoiceDate(end);
  if (a && b) return a === b ? a : `${a}–${b}`;
  return a || b || "";
}

export function periodLabelId(period) {
  const start = period?.start_date;
  const end = period?.end_date;
  if (start && end && start === end) return `${formatIdDate(start)} (WIB)`;
  if (start && end) return `${formatIdDate(start)} – ${formatIdDate(end)} (WIB)`;
  if (start) return `Dari ${formatIdDate(start)} (WIB)`;
  if (end) return `Sampai ${formatIdDate(end)} (WIB)`;
  return "Snapshot / tanpa filter tanggal";
}

/**
 * Build KPI summary cards from widget + kpi_summary.
 * Never invents numbers not present in the source payload.
 */
export function buildKpiCards(widget, kpi = {}, total) {
  const key = String(widget || "");
  const grain = String(kpi.grain || "");
  const cards = [];
  const primaryLabel = KPI_PRIMARY_LABEL[key] || kpi.label || "Total";
  if (kpi.status === "error") {
    return [{
      key: "incomplete",
      label: primaryLabel,
      value: "Data tidak lengkap",
      testId: "dr-kpi-incomplete",
      kind: "status",
    }];
  }

  const hasAmount = kpi.amount != null && kpi.amount !== "";
  const hasQty = kpi.qty_remaining != null && kpi.qty_remaining !== "";
  const count = kpi.count != null ? kpi.count : total;
  const lineCount = kpi.line_count;

  if (hasAmount) {
    cards.push({
      key: "amount",
      label: primaryLabel,
      value: formatIdRupiah(kpi.amount),
      testId: "dr-kpi-amount",
      kind: "currency",
    });
    if (count != null) {
      cards.push({
        key: "count",
        label: grain === "invoice" || key === "purchases" ? "Jumlah Faktur" : "Jumlah Baris",
        value: `${formatIdNumber(count)}${grain === "invoice" || key === "purchases" ? " faktur" : ""}`.trim(),
        testId: "dr-kpi-count",
        kind: "number",
      });
    }
  } else if (hasQty) {
    cards.push({
      key: "qty",
      label: primaryLabel,
      value: formatIdQty(kpi.qty_remaining, kpi.unit || ""),
      testId: "dr-kpi-qty",
      kind: "quantity",
    });
    if (count != null) {
      cards.push({
        key: "count",
        label: "Jumlah PO",
        value: `${formatIdNumber(count)} PO`,
        testId: "dr-kpi-count",
        kind: "number",
      });
    }
  } else if (grain === "line" || lineCount != null) {
    if (count != null) {
      cards.push({
        key: "count",
        label: primaryLabel,
        value: `${formatIdNumber(count)} PO`,
        testId: "dr-kpi-count",
        kind: "number",
      });
    }
    if (lineCount != null) {
      cards.push({
        key: "lines",
        label: "Jumlah Baris Item",
        value: formatIdNumber(lineCount),
        testId: "dr-kpi-lines",
        kind: "number",
      });
    }
  } else if (grain === "sj") {
    if (count != null) {
      cards.push({
        key: "count",
        label: primaryLabel,
        value: `${formatIdNumber(count)} SJ`,
        testId: "dr-kpi-count",
        kind: "number",
      });
    }
  } else if (grain === "journal" || key === "cash") {
    if (count != null) {
      cards.push({
        key: "count",
        label: primaryLabel,
        value: `${formatIdNumber(count)} mutasi`,
        testId: "dr-kpi-count",
        kind: "number",
      });
    }
    if (kpi.total_debit != null) {
      cards.push({
        key: "debit",
        label: "Total Debit",
        value: formatIdRupiah(kpi.total_debit),
        testId: "dr-kpi-debit",
        kind: "currency",
      });
    }
    if (kpi.total_credit != null) {
      cards.push({
        key: "credit",
        label: "Total Kredit",
        value: formatIdRupiah(kpi.total_credit),
        testId: "dr-kpi-credit",
        kind: "currency",
      });
    }
  } else if (count != null) {
    const suffix = grain === "po" || key.startsWith("po_") || key.startsWith("deadline") || key.startsWith("approval")
      ? " PO"
      : "";
    cards.push({
      key: "count",
      label: primaryLabel,
      value: `${formatIdNumber(count)}${suffix}`,
      testId: "dr-kpi-count",
      kind: "number",
    });
  }

  return cards;
}
