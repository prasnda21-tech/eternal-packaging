/**
 * Presentation helpers for the Pesanan Penjualan list and create summary.
 * Quantities stay grouped by the unit already stored on each line.
 * Status text is the existing delivery/workflow label — no invented states.
 */

import { salesOrderDeliveryStatus } from "./pesananPenjualanColumnFilters";
import {
  SALES_LIST_DEFAULT_PAGE_SIZE,
  SALES_LIST_PAGE_SIZES,
  normalizeSalesListTab,
  salesFiveTabListParams,
} from "./salesFiveTabs";

export const SALES_ORDER_PAGE_SIZES = [...SALES_LIST_PAGE_SIZES];
export const SALES_ORDER_DEFAULT_PAGE_SIZE = SALES_LIST_DEFAULT_PAGE_SIZE;

export function quantityByUnitFromLines(lines) {
  const totals = new Map();
  (lines || []).forEach((line) => {
    const unit = String(line?.unit || "").trim() || "pcs";
    const qty = Number(line?.quantity);
    if (!Number.isFinite(qty)) return;
    totals.set(unit, (totals.get(unit) || 0) + qty);
  });
  return Array.from(totals.entries()).map(([unit, quantity]) => ({ unit, quantity }));
}

export function formatQuantityByUnit(parts) {
  const rows = (parts || []).filter((part) => Number.isFinite(Number(part?.quantity)));
  if (!rows.length) return "—";
  return rows
    .map((part) => `${Number(part.quantity).toLocaleString("id-ID")} ${part.unit}`)
    .join(" · ");
}

export function orderQuantityLabel(order) {
  if (Array.isArray(order?.quantity_by_unit) && order.quantity_by_unit.length) {
    return formatQuantityByUnit(order.quantity_by_unit);
  }
  if (order?.total_quantity == null || order.total_quantity === "") return "—";
  const qty = Number(order.total_quantity);
  if (!Number.isFinite(qty)) return "—";
  return qty.toLocaleString("id-ID");
}

function finiteQty(value) {
  if (value == null || value === "") return null;
  const qty = Number(value);
  if (!Number.isFinite(qty) || qty < 0) return null;
  return qty;
}

function progressUnits(display) {
  if (!Array.isArray(display?.units)) return [];
  return display.units.map((row) => {
    const ordered = finiteQty(row?.ordered);
    const shipped = finiteQty(row?.shipped);
    const unit = String(row?.unit || "").trim();
    if (ordered == null || shipped == null || !unit) return null;
    return { ordered, shipped, unit };
  }).filter(Boolean);
}

function singleUnitLabel(row) {
  const unitSuffix = row.unit ? ` ${row.unit}` : "";
  return `${row.shipped.toLocaleString("id-ID")} terkirim / ${row.ordered.toLocaleString("id-ID")} pesanan${unitSuffix}`;
}

function compactUnitLabel(rows) {
  return rows
    .map((row) => `${row.shipped.toLocaleString("id-ID")}/${row.ordered.toLocaleString("id-ID")} ${row.unit}`)
    .join(" · ");
}

/**
 * Delivery progress for the Pengiriman column.
 * Prefer delivery_progress (valid shipment lines). delivery_summary stays the
 * eligibility snapshot and is only a fallback when the display payload is absent.
 * Each unit stays separate. A bar is drawn only for one verified unit.
 */
export function listDeliveryProgress(order) {
  const display = order?.delivery_progress;
  const useDisplay = !!(display && typeof display === "object" && display.status);
  const status = useDisplay ? String(display.status) : salesOrderDeliveryStatus(order);
  const parts = Array.isArray(order?.quantity_by_unit) ? order.quantity_by_unit : [];
  const summary = useDisplay ? display : order?.delivery_summary;
  const rows = useDisplay ? progressUnits(display) : [];
  const remainder = !!(useDisplay && display.unverified_remainder);
  if (useDisplay && rows.length === 0 && (status === "UNVERIFIED" || remainder)) {
    return { status, shipped: null, ordered: null, unit: "", ratio: null, label: "Perlu verifikasi" };
  }
  if (rows.length === 1 && !remainder) {
    const row = rows[0];
    const ratio = row.ordered > 0 ? Math.min(1, Math.max(0, row.shipped / row.ordered)) : 0;
    return { status, shipped: row.shipped, ordered: row.ordered, unit: row.unit, ratio, label: singleUnitLabel(row) };
  }
  if (rows.length > 1 || (rows.length === 1 && remainder)) {
    const numeric = rows.length === 1 ? singleUnitLabel(rows[0]) : compactUnitLabel(rows);
    const label = remainder ? `${numeric} · Perlu verifikasi` : numeric;
    return { status, shipped: null, ordered: null, unit: "", ratio: null, label };
  }
  if (useDisplay && status === "UNVERIFIED") {
    return { status, shipped: null, ordered: null, unit: "", ratio: null, label: "Perlu verifikasi" };
  }
  const shipped = summary && summary.total_shipped != null && summary.total_shipped !== ""
    ? Number(summary.total_shipped)
    : null;
  const ordered = summary && summary.total_ordered != null && summary.total_ordered !== ""
    ? Number(summary.total_ordered)
    : null;
  const numbersOk = parts.length === 1
    && Number.isFinite(shipped)
    && Number.isFinite(ordered)
    && ordered >= 0;
  if (!numbersOk) {
    return { status, shipped: null, ordered: null, unit: "", ratio: null, label: status };
  }
  const unit = String(parts[0].unit || "").trim();
  const ratio = ordered > 0 ? Math.min(1, Math.max(0, shipped / ordered)) : 0;
  const unitSuffix = unit ? ` ${unit}` : "";
  const label = `${shipped.toLocaleString("id-ID")} terkirim / ${ordered.toLocaleString("id-ID")} pesanan${unitSuffix}`;
  return { status, shipped, ordered, unit, ratio, label };
}

/** Delivery progress from the existing summary. Mixed units are not added together. */
export function listProgressText(order) {
  return listDeliveryProgress(order).label;
}

export function periodBounds(period, today = new Date()) {
  const year = today.getFullYear();
  const month = today.getMonth();
  const iso = (date) => {
    const y = date.getFullYear();
    const m = String(date.getMonth() + 1).padStart(2, "0");
    const d = String(date.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  };
  if (period === "this_month") {
    return { date_from: iso(new Date(year, month, 1)), date_to: iso(new Date(year, month + 1, 0)) };
  }
  if (period === "last_month") {
    return { date_from: iso(new Date(year, month - 1, 1)), date_to: iso(new Date(year, month, 0)) };
  }
  return { date_from: "", date_to: "" };
}

function csvParam(values) {
  if (!Array.isArray(values)) return undefined;
  const parts = values.map((item) => String(item || "").trim()).filter(Boolean);
  return parts.length ? parts.join(",") : undefined;
}

export function salesOrderListRequest({
  q,
  status,
  listTab,
  dateFrom,
  dateTo,
  orderDateFrom,
  orderDateTo,
  shipDateFrom,
  shipDateTo,
  customerQ,
  sales,
  createdBy,
  deliveryStatuses,
  processLabels,
  invoiceStatuses,
  qtyMin,
  qtyMax,
  qtyUnit,
  page,
  pageSize,
} = {}) {
  const salesKey = String(sales || "").trim();
  const creatorKey = String(createdBy || "").trim();
  const customerKey = String(customerQ || "").trim();
  const qtyUnitKey = String(qtyUnit || "").trim();
  const qtyMinKey = String(qtyMin ?? "").trim();
  const qtyMaxKey = String(qtyMax ?? "").trim();
  const extra = {
    date_from: dateFrom || undefined,
    date_to: dateTo || undefined,
    order_date_from: orderDateFrom || undefined,
    order_date_to: orderDateTo || undefined,
    ship_date_from: shipDateFrom || undefined,
    ship_date_to: shipDateTo || undefined,
    customer_q: customerKey || undefined,
    sales: salesKey || undefined,
    created_by: creatorKey || undefined,
    delivery_status: csvParam(deliveryStatuses),
    process_label: csvParam(processLabels),
    invoice_status: csvParam(invoiceStatuses),
    qty_min: qtyMinKey || undefined,
    qty_max: qtyMaxKey || undefined,
    qty_unit: qtyUnitKey || undefined,
  };
  // Prefer exclusive backend list_tab (five-tab). Legacy status kept only when listTab omitted.
  if (listTab != null && String(listTab).trim() !== "") {
    return salesFiveTabListParams({
      listTab,
      page,
      pageSize: pageSize || SALES_LIST_DEFAULT_PAGE_SIZE,
      q,
      extra,
    });
  }
  const params = { ...extra };
  const query = String(q || "").trim();
  if (query) params.q = query;
  if (status && status !== "all") params.status = status;
  if (page) params.page = page;
  if (pageSize) params.page_size = pageSize;
  return params;
}

/** Default secondary status token for a Pesanan Penjualan list tab (legacy compat). */
export function salesOrderTabDefaultStatus(listTab) {
  const tab = normalizeSalesListTab(listTab, { defaultTab: "active" });
  if (tab === "completed") return "selesai";
  if (tab === "cancelled") return "cancelled";
  if (tab === "all") return "gabungan";
  if (tab === "pending") return "pending";
  return "aktif";
}

/**
 * URL → five-tab id (all|active|completed|pending|cancelled).
 * Legacy aktif/histori/semua and status tokens map into the five-tab set.
 */
export function salesOrderListTabFromQuery({ tab, status } = {}) {
  const st = String(status || "").toLowerCase();
  const tb = String(tab || "").toLowerCase();
  // Explicit status wins over tab= (including tab=semua / all).
  if (st === "pending") return "pending";
  if (st === "cancelled" || st === "batal") return "cancelled";
  if (st === "selesai" || st === "completed" || st === "histori") return "completed";
  if (st === "hold" || st === "aktif" || st === "active") return "active";
  if (st === "all") return "active";
  if (st === "gabungan") return "all";
  if (tb === "pending") return "pending";
  if (tb === "cancelled" || tb === "batal") return "cancelled";
  if (tb === "completed" || tb === "selesai" || tb === "histori") return "completed";
  if (tb === "semua" || tb === "all") return "all";
  if (tb === "aktif" || tb === "active") return "active";
  if (tb) return normalizeSalesListTab(tb);
  return "active";
}

export function salesOrderStatusFromQuery({ tab, status } = {}) {
  if (status) return String(status);
  return salesOrderTabDefaultStatus(salesOrderListTabFromQuery({ tab, status }));
}

/**
 * "Pesanan aktif" summary uses the server total for the unfiltered active tab.
 * A single page is not a valid total.
 */
export function canShowAktifSummary({
  tab,
  status,
  q,
  period,
  columnFiltersActive,
  total,
  capped,
} = {}) {
  const normalized = normalizeSalesListTab(tab, { defaultTab: "active" });
  if (normalized !== "active") return false;
  if (status && status !== "aktif" && status !== "active") return false;
  if (String(q || "").trim()) return false;
  if (period && period !== "all") return false;
  if (columnFiltersActive) return false;
  if (capped) return false;
  if (total == null || total === "") return false;
  const count = Number(total);
  return Number.isFinite(count);
}

export function listPageCount(total, pageSize) {
  const size = Number(pageSize) || 1;
  const count = Number(total);
  if (!Number.isFinite(count) || count <= 0) return 1;
  return Math.max(1, Math.ceil(count / size));
}

export function listRangeLabel(total, page, pageSize) {
  const count = Number(total) || 0;
  const size = Number(pageSize) || 10;
  const current = Number(page) || 1;
  if (count <= 0) return "Menampilkan 0 pesanan";
  const from = (current - 1) * size + 1;
  const to = Math.min(count, current * size);
  return `Menampilkan ${from.toLocaleString("id-ID")}–${to.toLocaleString("id-ID")} dari ${count.toLocaleString("id-ID")} pesanan`;
}

/** Snapshot creator for list column. Empty → Tidak diketahui. Never use sales. */
export function orderCreatorLabel(order) {
  const text = String(order?.created_by_user ?? "").trim();
  return text || "Tidak diketahui";
}

const INVOICE_STATUS_LABELS = {
  belum: "Belum",
  sebagian: "Sebagian",
  sudah: "Sudah",
  perlu_pemeriksaan: "Perlu Pemeriksaan",
};

/** Invoice coverage chip label from list API. Null when hidden by RBAC. */
export function orderInvoiceStatusLabel(order) {
  if (!order || order.invoice_status == null || order.invoice_status === "") {
    if (order?.invoice_status_label) return String(order.invoice_status_label);
    return null;
  }
  const key = String(order.invoice_status).trim().toLowerCase();
  if (order.invoice_status_label) return String(order.invoice_status_label);
  return INVOICE_STATUS_LABELS[key] || null;
}

export function orderInvoiceStatusTone(order) {
  const key = String(order?.invoice_status || "").trim().toLowerCase();
  if (key === "sudah") return "done";
  if (key === "sebagian") return "partial";
  if (key === "perlu_pemeriksaan") return "review";
  if (key === "belum") return "open";
  return "open";
}
