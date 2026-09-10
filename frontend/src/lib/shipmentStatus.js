/** Display helpers for Status Pengiriman progress cells. */

import { formatDisplayDate } from "./constants";

export const EMPTY_SHIP_FILTERS = {
  customer: "",
  po: "",
  orderDateFrom: "",
  orderDateTo: "",
  deliveryDateFrom: "",
  deliveryDateTo: "",
  deliveryDatePresence: "all",
  cabang: "",
  gudang: "",
  terkirim: "all",
  dapatDikirim: "all",
};

/** @deprecated Prefer EMPTY_SHIP_FILTERS; kept for callers that still pass orderDate/deliveryDate. */
export const EMPTY_FILTERS = EMPTY_SHIP_FILTERS;

export function dateKey(iso) {
  if (!iso) return "";
  const text = String(iso).trim();
  if (/^\d{4}-\d{2}-\d{2}/.test(text)) return text.slice(0, 10);
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

/** Canonical uncapped percent used for Terkirim filter predicates. */
export function shipDeliveredPercent(row) {
  const raw = row?.percent_delivered;
  if (raw != null && Number.isFinite(Number(raw))) return Number(raw);
  const display = row?.percent_delivered_display;
  return Number.isFinite(Number(display)) ? Number(display) : 0;
}

/** Canonical uncapped percent used for Dapat Dikirim filter predicates. */
export function shipReadyPercent(row) {
  const raw = row?.percent_ready_to_ship;
  if (raw != null && Number.isFinite(Number(raw))) return Number(raw);
  const display = row?.percent_ready_to_ship_display;
  return Number.isFinite(Number(display)) ? Number(display) : 0;
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

function inDateRange(key, from, to) {
  if (!from && !to) return true;
  if (!key) return false;
  if (from && key < from) return false;
  if (to && key > to) return false;
  return true;
}

/** Normalize legacy single-date keys into from/to ranges. */
export function normalizeShipFilters(filters = {}) {
  const f = { ...EMPTY_SHIP_FILTERS, ...(filters || {}) };
  const orderExact = (f.orderDate || "").trim();
  const deliveryExact = (f.deliveryDate || "").trim();
  if (orderExact && !f.orderDateFrom && !f.orderDateTo) {
    f.orderDateFrom = orderExact;
    f.orderDateTo = orderExact;
  }
  if (deliveryExact && !f.deliveryDateFrom && !f.deliveryDateTo) {
    f.deliveryDateFrom = deliveryExact;
    f.deliveryDateTo = deliveryExact;
  }
  return f;
}

export function matchesShipFilters(row, filters) {
  const f = normalizeShipFilters(filters);
  const customer = (f.customer || "").trim().toLowerCase();
  const po = (f.po || "").trim().toLowerCase();
  const orderFrom = (f.orderDateFrom || "").trim();
  const orderTo = (f.orderDateTo || "").trim();
  const deliveryFrom = (f.deliveryDateFrom || "").trim();
  const deliveryTo = (f.deliveryDateTo || "").trim();
  const presence = (f.deliveryDatePresence || "all").trim() || "all";
  const cabang = (f.cabang || "").trim();
  const gudang = (f.gudang || "").trim();

  if (customer && !(row.customer_name || "").toLowerCase().includes(customer)) return false;
  if (po && !(row.order_number || "").toLowerCase().includes(po)) return false;

  const orderKey = dateKey(row.order_date);
  if (!inDateRange(orderKey, orderFrom, orderTo)) return false;

  const deliveryKey = dateKey(row.delivery_date);
  if (presence === "has" && !deliveryKey) return false;
  if (presence === "none" && deliveryKey) return false;
  if (presence !== "none" && !inDateRange(deliveryKey, deliveryFrom, deliveryTo)) return false;

  // Cabang/gudang not present in dataset — only apply if a real value is chosen.
  if (cabang && (row.cabang || row.branch || "") !== cabang) return false;
  if (gudang && (row.gudang || row.warehouse || "") !== gudang) return false;

  if (!matchesProgressBand(shipDeliveredPercent(row), f.terkirim)) return false;
  if (!matchesProgressBand(shipReadyPercent(row), f.dapatDikirim)) return false;
  return true;
}

/** Count meaningful advanced filter groups (not matching row count). */
export function countActiveShipFilters(filters) {
  const f = normalizeShipFilters(filters);
  let n = 0;
  if ((f.customer || "").trim()) n += 1;
  if ((f.po || "").trim()) n += 1;
  if ((f.orderDateFrom || "").trim() || (f.orderDateTo || "").trim()) n += 1;
  if (
    (f.deliveryDateFrom || "").trim()
    || (f.deliveryDateTo || "").trim()
    || ((f.deliveryDatePresence || "all") !== "all")
  ) n += 1;
  if ((f.cabang || "").trim()) n += 1;
  if ((f.gudang || "").trim()) n += 1;
  if ((f.terkirim || "all") !== "all") n += 1;
  if ((f.dapatDikirim || "all") !== "all") n += 1;
  return n;
}

export const EMPTY_SHIP_SORT = { key: null, direction: null };

export const SHIP_SORT_KEYS = {
  customer: "customer",
  po_number: "po_number",
  order_date: "order_date",
  delivery_date: "delivery_date",
  shipped: "shipped",
  shippable: "shippable",
};

/** Header click cycle: ASC → DESC → NONE. Switching column starts ASC. */
export function cycleShipSort(current, key) {
  if (!key) return { ...EMPTY_SHIP_SORT };
  if (!current || current.key !== key || !current.direction) {
    return { key, direction: "asc" };
  }
  if (current.direction === "asc") {
    return { key, direction: "desc" };
  }
  return { ...EMPTY_SHIP_SORT };
}

function emptyLastCompare(aEmpty, bEmpty, valueCmp, direction) {
  if (aEmpty && bEmpty) return 0;
  if (aEmpty) return 1;
  if (bEmpty) return -1;
  if (valueCmp === 0) return 0;
  return direction === "desc" ? -valueCmp : valueCmp;
}

function compareShipSortValues(a, b, key, direction) {
  if (key === SHIP_SORT_KEYS.customer) {
    const na = (a?.customer_name || "").trim();
    const nb = (b?.customer_name || "").trim();
    return emptyLastCompare(
      !na,
      !nb,
      na.localeCompare(nb, "id", { sensitivity: "base", numeric: true }),
      direction,
    );
  }
  if (key === SHIP_SORT_KEYS.po_number) {
    const na = String(a?.order_number || "").trim();
    const nb = String(b?.order_number || "").trim();
    return emptyLastCompare(
      !na,
      !nb,
      na.localeCompare(nb, "id", { numeric: true, sensitivity: "base" }),
      direction,
    );
  }
  if (key === SHIP_SORT_KEYS.order_date) {
    const ka = dateKey(a?.order_date);
    const kb = dateKey(b?.order_date);
    return emptyLastCompare(!ka, !kb, ka.localeCompare(kb), direction);
  }
  if (key === SHIP_SORT_KEYS.delivery_date) {
    const ka = dateKey(a?.delivery_date);
    const kb = dateKey(b?.delivery_date);
    return emptyLastCompare(!ka, !kb, ka.localeCompare(kb), direction);
  }
  if (key === "effective_delivery_date") {
    const ka = dateKey(a?.effective_delivery_date);
    const kb = dateKey(b?.effective_delivery_date);
    return emptyLastCompare(!ka, !kb, ka.localeCompare(kb), direction);
  }
  if (key === "deadline") {
    const ka = dateKey(a?.deadline);
    const kb = dateKey(b?.deadline);
    return emptyLastCompare(!ka, !kb, ka.localeCompare(kb), direction);
  }
  if (key === "jadwal_manual") {
    const ka = dateKey(a?.jadwal_manual);
    const kb = dateKey(b?.jadwal_manual);
    return emptyLastCompare(!ka, !kb, ka.localeCompare(kb), direction);
  }
  if (key === "driver") {
    const na = String(a?.driver_name || "").trim();
    const nb = String(b?.driver_name || "").trim();
    return emptyLastCompare(
      !na,
      !nb,
      na.localeCompare(nb, "id", { sensitivity: "base", numeric: true }),
      direction,
    );
  }
  if (key === "status") {
    const na = String(a?.status_label || a?.status || "").trim();
    const nb = String(b?.status_label || b?.status || "").trim();
    return emptyLastCompare(
      !na,
      !nb,
      na.localeCompare(nb, "id", { sensitivity: "base" }),
      direction,
    );
  }
  if (key === SHIP_SORT_KEYS.shipped) {
    const va = shipDeliveredPercent(a);
    const vb = shipDeliveredPercent(b);
    return emptyLastCompare(false, false, va === vb ? 0 : (va < vb ? -1 : 1), direction);
  }
  if (key === SHIP_SORT_KEYS.shippable) {
    const va = shipReadyPercent(a);
    const vb = shipReadyPercent(b);
    return emptyLastCompare(false, false, va === vb ? 0 : (va < vb ? -1 : 1), direction);
  }
  return 0;
}

/**
 * Sort filtered rows by active header sort. Does not mutate source.
 * NONE / missing sort returns the same array reference (default order).
 * Equal primary values keep prior relative order (stable sort).
 */
export function sortShipRows(rows, sort = EMPTY_SHIP_SORT) {
  const list = Array.isArray(rows) ? rows : [];
  if (!sort?.key || !sort?.direction) return list;
  const next = list.slice();
  next.sort((a, b) => compareShipSortValues(a, b, sort.key, sort.direction));
  return next;
}

/** @deprecated Prefer sortShipRows({ key: 'customer', direction }). */
export function sortShipRowsByCustomer(rows, direction = "asc") {
  return sortShipRows(rows, { key: SHIP_SORT_KEYS.customer, direction });
}

export function clampPercent(n) {
  const v = Number(n);
  if (!Number.isFinite(v) || v <= 0) return 0;
  return Math.min(100, Math.round(v));
}

/** Terkirim tone: empty / low orange / mid-high yellow / full green. */
export function terkirimTone(pct) {
  const p = clampPercent(pct);
  if (p <= 0) return "empty";
  if (p >= 100) return "full";
  if (p < 50) return "low";
  return "mid";
}

export function formatShipDate(iso) {
  return formatDisplayDate(iso);
}
