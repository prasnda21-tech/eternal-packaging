/** Display helpers for Status Pengiriman progress cells. */

import { formatDisplayDate } from "./constants";

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

export function matchesShipFilters(row, filters) {
  const customer = (filters?.customer || "").trim().toLowerCase();
  const po = (filters?.po || "").trim().toLowerCase();
  const orderDate = (filters?.orderDate || "").trim();
  const deliveryDate = (filters?.deliveryDate || "").trim();
  if (customer && !(row.customer_name || "").toLowerCase().includes(customer)) return false;
  if (po && !(row.order_number || "").toLowerCase().includes(po)) return false;
  if (orderDate && dateKey(row.order_date) !== orderDate) return false;
  if (deliveryDate && dateKey(row.delivery_date) !== deliveryDate) return false;
  return true;
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
