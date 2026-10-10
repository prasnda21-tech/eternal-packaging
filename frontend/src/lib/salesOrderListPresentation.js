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

/**
 * Delivery progress from shipped vs ordered on the existing summary.
 * Mixed units are not added into one ratio. A zero shipped count is ratio 0.
 */
export function listDeliveryProgress(order) {
  const status = salesOrderDeliveryStatus(order);
  const parts = Array.isArray(order?.quantity_by_unit) ? order.quantity_by_unit : [];
  const summary = order?.delivery_summary;
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

export function salesOrderListRequest({
  q,
  status,
  listTab,
  dateFrom,
  dateTo,
  tanggalKirimPreset,
  sales,
  deliveryStatuses,
  page,
  pageSize,
} = {}) {
  const shipPreset = String(tanggalKirimPreset || "all").trim() || "all";
  const salesKey = String(sales || "").trim();
  const statuses = Array.isArray(deliveryStatuses)
    ? deliveryStatuses.map((item) => String(item || "").trim()).filter(Boolean)
    : [];
  // Prefer exclusive backend list_tab (five-tab). Legacy status kept only when listTab omitted.
  if (listTab != null && String(listTab).trim() !== "") {
    return salesFiveTabListParams({
      listTab,
      page,
      pageSize: pageSize || SALES_LIST_DEFAULT_PAGE_SIZE,
      q,
      extra: {
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
        tanggal_pengiriman_preset: shipPreset !== "all" ? shipPreset : undefined,
        sales: salesKey || undefined,
        delivery_status: statuses.length ? statuses.join(",") : undefined,
      },
    });
  }
  const params = {};
  const query = String(q || "").trim();
  if (query) params.q = query;
  if (status && status !== "all") params.status = status;
  if (dateFrom) params.date_from = dateFrom;
  if (dateTo) params.date_to = dateTo;
  if (shipPreset !== "all") params.tanggal_pengiriman_preset = shipPreset;
  if (salesKey) params.sales = salesKey;
  if (statuses.length) params.delivery_status = statuses.join(",");
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
