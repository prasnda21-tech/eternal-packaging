/** Delivery schedule display helpers. Dates are YYYY-MM-DD keys. */

import {
  EMPTY_SHIP_SORT,
  countActiveShipFilters,
  cycleShipSort,
  shipDeliveredPercent,
  shipReadyPercent,
  sortShipRows,
} from "./shipmentStatus";

export function dateKey(raw) {
  if (!raw) return "";
  const text = String(raw).trim();
  if (text.length >= 10 && text[4] === "-" && text[7] === "-") return text.slice(0, 10);
  return "";
}

function matchesProgressBand(pct, band) {
  const key = (band || "all").trim() || "all";
  if (key === "all") return true;
  const p = Number(pct) || 0;
  if (key === "none") return p <= 0;
  if (key === "partial") return p > 0 && p < 100;
  if (key === "full") return p >= 100;
  return true;
}

export function matchesDeliveryScheduleFilters(row, filters = {}) {
  const po = (filters.po || "").trim().toLowerCase();
  if (po && !String(row.order_number || "").toLowerCase().includes(po)) return false;
  const customer = (filters.customer || "").trim().toLowerCase();
  if (customer && !String(row.customer_name || "").toLowerCase().includes(customer)) return false;
  const status = (filters.status || "").trim().toLowerCase();
  if (status && status !== "semua" && row.status !== status) return false;
  const from = dateKey(filters.dateFrom);
  const to = dateKey(filters.dateTo);
  const eff = dateKey(row.effective_delivery_date);
  if (from && (!eff || eff < from)) return false;
  if (to && (!eff || eff > to)) return false;
  const driver = (filters.driver || "").trim().toLowerCase();
  if (driver && !String(row.driver_name || "").toLowerCase().includes(driver)) return false;
  if (!matchesProgressBand(shipDeliveredPercent(row), filters.terkirim)) return false;
  if (!matchesProgressBand(shipReadyPercent(row), filters.dapatDikirim)) return false;
  return true;
}

export const DELIVERY_SCHEDULE_STATUS_OPTIONS = [
  { key: "semua", label: "Semua status" },
  { key: "belum_dijadwalkan", label: "Belum Dijadwalkan" },
  { key: "terjadwal", label: "Terjadwal" },
  { key: "hari_ini", label: "Hari Ini" },
  { key: "terlambat", label: "Terlambat" },
  { key: "terkirim", label: "Terkirim" },
];

export const EMPTY_DELIVERY_FILTERS = {
  dateFrom: "",
  dateTo: "",
  po: "",
  customer: "",
  status: "semua",
  driver: "",
  terkirim: "all",
  dapatDikirim: "all",
};

export const EMPTY_DELIVERY_SORT = EMPTY_SHIP_SORT;

export const DELIVERY_SORT_KEYS = {
  customer: "customer",
  po_number: "po_number",
  effective_delivery_date: "effective_delivery_date",
  deadline: "deadline",
  jadwal_manual: "jadwal_manual",
  status: "status",
  driver: "driver",
  shipped: "shipped",
  shippable: "shippable",
};

export function cycleDeliverySort(current, key) {
  return cycleShipSort(current, key);
}

export function sortDeliveryRows(rows, sort = EMPTY_DELIVERY_SORT) {
  return sortShipRows(rows, sort);
}

/** Count meaningful advanced filter groups (not matching row count). */
export function countActiveDeliveryFilters(filters = {}) {
  const f = { ...EMPTY_DELIVERY_FILTERS, ...(filters || {}) };
  let n = 0;
  if ((f.customer || "").trim()) n += 1;
  if ((f.po || "").trim()) n += 1;
  if ((f.dateFrom || "").trim() || (f.dateTo || "").trim()) n += 1;
  if ((f.status || "semua") !== "semua") n += 1;
  if ((f.driver || "").trim()) n += 1;
  if ((f.terkirim || "all") !== "all") n += 1;
  if ((f.dapatDikirim || "all") !== "all") n += 1;
  return n;
}

// Re-export for tests that assert shared ship helpers remain available.
export { countActiveShipFilters, shipDeliveredPercent, shipReadyPercent };

export function rescheduleConfirmLabel(canSetDate) {
  return canSetDate ? "Konfirmasi Jadwal Ulang" : "Konfirmasi Permintaan";
}
