import { STAGES } from "./constants";
import { shippingDatePresetActive } from "./salesDatePresets";

export const SALES_ORDER_STATUS_FILTERS = [
  { id: "all", label: "Semua" },
  { id: "aktif", label: "Aktif" },
  { id: "selesai", label: "Selesai" },
  { id: "hold", label: "HOLD" },
  { id: "cancelled", label: "Batal" },
];

/** Normalized delivery progress options for header multi-select. */
export const SALES_ORDER_DELIVERY_STATUS_OPTIONS = [
  { value: "BELUM DIKIRIM", label: "Belum dikirim" },
  { value: "SEBAGIAN", label: "Sebagian" },
  { value: "SELESAI", label: "Selesai" },
  { value: "DIBATALKAN", label: "Dibatalkan" },
];

export const EMPTY_SALES_ORDER_COLUMN_FILTERS = {
  tanggalKirimPreset: "all",
  deliveryStatuses: [],
  sales: "",
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

export function salesOrderColumnFilterActive(key, filters, statusFilter = "all") {
  const f = filters || EMPTY_SALES_ORDER_COLUMN_FILTERS;
  switch (key) {
    case "tanggalKirim":
      return shippingDatePresetActive(f.tanggalKirimPreset);
    case "delivery":
      return Array.isArray(f.deliveryStatuses) && f.deliveryStatuses.length > 0;
    case "sales":
      return !!String(f.sales || "").trim();
    case "stage":
      return !!(statusFilter && statusFilter !== "all");
    default:
      return false;
  }
}

export function salesOrderColumnFiltersAreActive(filters, statusFilter = "all") {
  return ["tanggalKirim", "delivery", "sales", "stage"].some((key) =>
    salesOrderColumnFilterActive(key, filters, statusFilter)
  );
}
