import { STAGES } from "./constants";

export const SALES_ORDER_STATUS_FILTERS = [
  { id: "all", label: "Semua" },
  { id: "aktif", label: "Aktif" },
  { id: "selesai", label: "Selesai" },
  { id: "hold", label: "HOLD" },
  { id: "cancelled", label: "Batal" },
];

export const DEFAULT_DELIVERY_STATUS_OPTIONS = [
  "BELUM DIKIRIM",
  "SEBAGIAN",
  "SEBAGIAN DIKIRIM",
  "SELESAI",
  "SELESAI PENGIRIMAN",
  "DIBATALKAN",
];

export const EMPTY_SALES_ORDER_COLUMN_FILTERS = {
  po: "",
  dateFrom: "",
  dateTo: "",
  customer: "",
  outlet: "",
  qtyMin: "",
  qtyMax: "",
  remainMin: "",
  remainMax: "",
  deliveryStatuses: [],
  sales: "",
  stageText: "",
};

const ISO_DATE_PREFIX_RE = /^(\d{4})-(\d{2})-(\d{2})/;

function salesOrderStageIndex(value) {
  if (typeof value !== "number" || !Number.isInteger(value)) return null;
  if (value < 0 || value >= STAGES.length) return null;
  return value;
}

export function salesOrderIsoDate(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  const prefix = text.match(ISO_DATE_PREFIX_RE);
  if (prefix) return `${prefix[1]}-${prefix[2]}-${prefix[3]}`;
  try {
    const d = new Date(text);
    if (Number.isNaN(d.getTime())) return "";
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return `${y}-${m}-${day}`;
  } catch {
    return "";
  }
}

export function salesOrderRemainingQty(order) {
  const summary = order?.delivery_summary;
  if (summary && summary.total_remaining != null && summary.total_remaining !== "") {
    return Number(summary.total_remaining);
  }
  return Number(order?.total_quantity || 0);
}

export function salesOrderDeliveryStatus(order) {
  const status = order?.delivery_summary?.status;
  if (status) return String(status);
  return order?.cancelled ? "DIBATALKAN" : "BELUM DIKIRIM";
}

export function salesOrderStageLabel(order) {
  if (order?.cancelled) return "Cancelled";
  if (order?.hold) return "HOLD";
  const stage = salesOrderStageIndex(order?.current_stage);
  if (stage != null) return STAGES[stage];
  if (order?.status_label) return String(order.status_label);
  return "";
}

export function salesOrderSalesName(order) {
  return String(order?.sales || order?.sales_user || "");
}

function textContains(haystack, needle) {
  const q = String(needle || "").trim().toLowerCase();
  if (!q) return true;
  return String(haystack || "").toLowerCase().includes(q);
}

function inDateRange(isoDate, from, to) {
  if (!from && !to) return true;
  if (!isoDate) return false;
  if (from && isoDate < from) return false;
  if (to && isoDate > to) return false;
  return true;
}

function inNumericRange(value, minRaw, maxRaw) {
  const minText = String(minRaw ?? "").trim();
  const maxText = String(maxRaw ?? "").trim();
  if (!minText && !maxText) return true;
  const n = Number(value);
  if (!Number.isFinite(n)) return false;
  if (minText) {
    const min = Number(minText);
    if (Number.isFinite(min) && n < min) return false;
  }
  if (maxText) {
    const max = Number(maxText);
    if (Number.isFinite(max) && n > max) return false;
  }
  return true;
}

function deliveryStatusSelected(selected, status) {
  if (!selected || selected.length === 0) return true;
  const current = String(status || "").trim().toLowerCase();
  return selected.some((item) => String(item || "").trim().toLowerCase() === current);
}

export function salesOrderColumnFilterActive(key, filters, statusFilter = "all") {
  const f = filters || EMPTY_SALES_ORDER_COLUMN_FILTERS;
  switch (key) {
    case "po":
      return !!String(f.po || "").trim();
    case "date":
      return !!(f.dateFrom || f.dateTo);
    case "customer":
      return !!String(f.customer || "").trim();
    case "outlet":
      return !!String(f.outlet || "").trim();
    case "qty":
      return !!(String(f.qtyMin || "").trim() || String(f.qtyMax || "").trim());
    case "remain":
      return !!(String(f.remainMin || "").trim() || String(f.remainMax || "").trim());
    case "delivery":
      return Array.isArray(f.deliveryStatuses) && f.deliveryStatuses.length > 0;
    case "sales":
      return !!String(f.sales || "").trim();
    case "stage":
      return (statusFilter && statusFilter !== "all") || !!String(f.stageText || "").trim();
    default:
      return false;
  }
}

export function salesOrderColumnFiltersAreActive(filters, statusFilter = "all") {
  return [
    "po",
    "date",
    "customer",
    "outlet",
    "qty",
    "remain",
    "delivery",
    "sales",
    "stage",
  ].some((key) => salesOrderColumnFilterActive(key, filters, statusFilter));
}

export function applySalesOrderColumnFilters(orders, filters) {
  const f = filters || EMPTY_SALES_ORDER_COLUMN_FILTERS;
  const list = Array.isArray(orders) ? orders : [];
  const hasAny =
    salesOrderColumnFilterActive("po", f) ||
    salesOrderColumnFilterActive("date", f) ||
    salesOrderColumnFilterActive("customer", f) ||
    salesOrderColumnFilterActive("outlet", f) ||
    salesOrderColumnFilterActive("qty", f) ||
    salesOrderColumnFilterActive("remain", f) ||
    salesOrderColumnFilterActive("delivery", f) ||
    salesOrderColumnFilterActive("sales", f) ||
    !!String(f.stageText || "").trim();
  if (!hasAny) return list;

  return list.filter((order) => {
    if (!textContains(order?.po_number, f.po)) return false;
    if (!inDateRange(salesOrderIsoDate(order?.order_date), f.dateFrom, f.dateTo)) return false;
    if (!textContains(order?.customer, f.customer)) return false;
    if (!textContains(order?.outlet, f.outlet)) return false;
    if (!inNumericRange(Number(order?.total_quantity || 0), f.qtyMin, f.qtyMax)) return false;
    if (!inNumericRange(salesOrderRemainingQty(order), f.remainMin, f.remainMax)) return false;
    if (!deliveryStatusSelected(f.deliveryStatuses, salesOrderDeliveryStatus(order))) return false;
    if (!textContains(salesOrderSalesName(order), f.sales)) return false;
    if (!textContains(salesOrderStageLabel(order), f.stageText)) return false;
    return true;
  });
}

export function uniqueDeliveryStatuses(orders) {
  const seen = new Set();
  DEFAULT_DELIVERY_STATUS_OPTIONS.forEach((item) => seen.add(item));
  (orders || []).forEach((order) => {
    const status = salesOrderDeliveryStatus(order);
    if (status) seen.add(status);
  });
  return Array.from(seen);
}
