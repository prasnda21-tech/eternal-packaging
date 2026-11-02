import { STAGES } from "./constants";
import { operationalSalesSnapshotLabel } from "./operationalSalesSnapshot";

export const SALES_ORDER_DELIVERY_STATUS_OPTIONS = [
  { value: "BELUM DIKIRIM", label: "Belum dikirim" },
  { value: "SEBAGIAN", label: "Sebagian" },
  { value: "SELESAI", label: "Selesai" },
  { value: "DIBATALKAN", label: "Dibatalkan" },
];

export const SALES_ORDER_INVOICE_STATUS_OPTIONS = [
  { value: "belum", label: "Belum" },
  { value: "sebagian", label: "Sebagian" },
  { value: "sudah", label: "Sudah" },
  { value: "perlu_pemeriksaan", label: "Perlu Pemeriksaan" },
];

export const SALES_ORDER_PROCESS_LABEL_OPTIONS = [
  { value: "HOLD", label: "HOLD" },
  { value: "Cancelled", label: "Cancelled" },
  ...STAGES.map((label) => ({ value: label, label })),
];

export const CREATOR_UNKNOWN_TOKEN = "__unknown__";

export const EMPTY_SALES_ORDER_COLUMN_FILTERS = {
  customerQ: "",
  orderDateFrom: "",
  orderDateTo: "",
  shipDateFrom: "",
  shipDateTo: "",
  qtyMin: "",
  qtyMax: "",
  qtyUnit: "",
  deliveryStatuses: [],
  processLabels: [],
  sales: "",
  createdBy: "",
  invoiceStatuses: [],
};

function salesOrderStageIndex(value) {
  if (typeof value !== "number" || !Number.isInteger(value)) return null;
  if (value < 0 || value >= STAGES.length) return null;
  return value;
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

/** Same priority as list badge: Cancelled → HOLD → stage → status_label. */
export function salesOrderStageLabel(order) {
  if (order?.cancelled) return "Cancelled";
  if (order?.hold) return "HOLD";
  const stage = salesOrderStageIndex(order?.current_stage);
  if (stage != null) return STAGES[stage];
  if (order?.status_label) return String(order.status_label);
  return "";
}

export function salesOrderSalesName(order) {
  return operationalSalesSnapshotLabel(order);
}

function hasText(value) {
  return String(value || "").trim() !== "";
}

function dateRangeInvalid(from, to) {
  const a = String(from || "").trim();
  const b = String(to || "").trim();
  return !!(a && b && a > b);
}

function qtyRangeInvalid(min, max) {
  const loRaw = String(min ?? "").trim();
  const hiRaw = String(max ?? "").trim();
  if (!loRaw && !hiRaw) return false;
  const lo = loRaw === "" ? null : Number(loRaw);
  const hi = hiRaw === "" ? null : Number(hiRaw);
  if (loRaw !== "" && (!Number.isFinite(lo) || lo < 0)) return true;
  if (hiRaw !== "" && (!Number.isFinite(hi) || hi < 0)) return true;
  if (lo != null && hi != null && lo > hi) return true;
  return false;
}

export function salesOrderColumnFilterActive(key, filters) {
  const f = filters || EMPTY_SALES_ORDER_COLUMN_FILTERS;
  switch (key) {
    case "customer":
      return hasText(f.customerQ);
    case "orderDate":
      return hasText(f.orderDateFrom) || hasText(f.orderDateTo);
    case "shipDate":
    case "tanggalKirim":
      return hasText(f.shipDateFrom) || hasText(f.shipDateTo);
    case "qty":
      return hasText(f.qtyMin) || hasText(f.qtyMax) || hasText(f.qtyUnit);
    case "delivery":
      return Array.isArray(f.deliveryStatuses) && f.deliveryStatuses.length > 0;
    case "process":
    case "stage":
      return Array.isArray(f.processLabels) && f.processLabels.length > 0;
    case "sales":
      return hasText(f.sales);
    case "creator":
      return hasText(f.createdBy);
    case "invoice":
      return Array.isArray(f.invoiceStatuses) && f.invoiceStatuses.length > 0;
    default:
      return false;
  }
}

export function salesOrderColumnFiltersAreActive(filters) {
  return [
    "customer",
    "orderDate",
    "shipDate",
    "qty",
    "delivery",
    "process",
    "sales",
    "creator",
    "invoice",
  ].some((key) => salesOrderColumnFilterActive(key, filters));
}

export function salesOrderColumnFilterValidation(filters) {
  const f = filters || EMPTY_SALES_ORDER_COLUMN_FILTERS;
  if (dateRangeInvalid(f.orderDateFrom, f.orderDateTo)) {
    return "Tanggal PO: Dari tidak boleh lebih besar dari Sampai.";
  }
  if (dateRangeInvalid(f.shipDateFrom, f.shipDateTo)) {
    return "Tanggal Kirim: Dari tidak boleh lebih besar dari Sampai.";
  }
  if (qtyRangeInvalid(f.qtyMin, f.qtyMax)) {
    return "Qty: rentang minimum/maksimum tidak valid.";
  }
  return "";
}
