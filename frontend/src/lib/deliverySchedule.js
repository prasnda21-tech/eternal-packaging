/** Delivery schedule display helpers. Dates are YYYY-MM-DD keys. */

import { locationSetupSortValue } from "./deliveryRouteBridge";
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

/** Asia/Jakarta (WIB, UTC+7) calendar date. Independent of host timezone. */
export function wibTodayKey(now = new Date()) {
  const instant = now instanceof Date ? now : new Date(now);
  const wib = new Date(instant.getTime() + 7 * 60 * 60 * 1000);
  const y = wib.getUTCFullYear();
  const m = String(wib.getUTCMonth() + 1).padStart(2, "0");
  const d = String(wib.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function resolveFilterTodayKey(today) {
  if (typeof today === "string" && dateKey(today)) return dateKey(today);
  if (today instanceof Date && !Number.isNaN(today.getTime())) return wibTodayKey(today);
  return wibTodayKey();
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

/** Same date key as Tanggal Pengiriman column — SJ date first, then schedule effective. */
export function scheduleRowDeliveryDateKey(row) {
  return dateKey(row?.sj_delivery_date || row?.effective_delivery_date);
}

export function matchesDeliveryScheduleFilters(row, filters = {}, today = null) {
  // Combined toolbar search: match No PO OR customer name.
  const q = (filters.q || "").trim().toLowerCase();
  if (q) {
    const poHit = String(row.order_number || "").toLowerCase().includes(q);
    const customerHit = String(row.customer_name || "").toLowerCase().includes(q);
    if (!poHit && !customerHit) return false;
  }
  // Legacy / advanced separate fields remain AND when set (session restore).
  const po = (filters.po || "").trim().toLowerCase();
  if (po && !String(row.order_number || "").toLowerCase().includes(po)) return false;
  const customer = (filters.customer || "").trim().toLowerCase();
  if (customer && !String(row.customer_name || "").toLowerCase().includes(customer)) return false;
  // Display date for Tanggal Pengiriman — keep filter + column identical.
  const deliveryDate = scheduleRowDeliveryDateKey(row);
  const status = (filters.status || "").trim().toLowerCase();
  if (status && status !== "semua") {
    if (status === "hari_ini") {
      if (deliveryDate !== resolveFilterTodayKey(today)) return false;
    } else if (row.status !== status) {
      return false;
    }
  }
  const from = dateKey(filters.dateFrom);
  const to = dateKey(filters.dateTo);
  if (from && (!deliveryDate || deliveryDate < from)) return false;
  if (to && (!deliveryDate || deliveryDate > to)) return false;
  const driver = (filters.driver || "").trim().toLowerCase();
  if (driver && !String(row.driver_name || "").toLowerCase().includes(driver)) return false;
  if (!matchesProgressBand(shipDeliveredPercent(row), filters.terkirim)) return false;
  if (!matchesProgressBand(shipReadyPercent(row), filters.dapatDikirim)) return false;
  return true;
}

/** Active status filter options. Belum Dijadwalkan is hidden — schedule API only returns dated SJ rows. */
export const DELIVERY_SCHEDULE_STATUS_OPTIONS = [
  { key: "semua", label: "Semua status" },
  { key: "terjadwal", label: "Terjadwal" },
  { key: "hari_ini", label: "Tanggal Hari Ini" },
  { key: "terlambat", label: "Terlambat" },
  { key: "terkirim", label: "Terkirim" },
];

/** Legacy keys normalized away when loaded from URL / session storage. */
export const DELIVERY_SCHEDULE_LEGACY_STATUS_KEYS = Object.freeze(["belum_dijadwalkan"]);

export const EMPTY_DELIVERY_FILTERS = {
  q: "",
  dateFrom: "",
  dateTo: "",
  po: "",
  customer: "",
  status: "semua",
  driver: "",
  terkirim: "all",
  dapatDikirim: "all",
};

const ACTIVE_STATUS_KEYS = new Set(DELIVERY_SCHEDULE_STATUS_OPTIONS.map((o) => o.key));

/** Map unsupported / legacy status keys to "semua". Keeps bookmark key hari_ini. */
export function normalizeDeliveryScheduleStatus(status) {
  const key = String(status || "").trim().toLowerCase();
  if (!key || key === "semua") return "semua";
  if (ACTIVE_STATUS_KEYS.has(key)) return key;
  return "semua";
}

/** Normalize applied/draft filter bag. Sorts date bounds; never persists legacy status. */
export function normalizeDeliveryScheduleFilters(filters = {}) {
  const next = { ...EMPTY_DELIVERY_FILTERS, ...(filters || {}) };
  next.q = String(next.q || "");
  next.po = String(next.po || "");
  next.customer = String(next.customer || "");
  next.driver = String(next.driver || "");
  next.terkirim = (next.terkirim || "all").trim() || "all";
  next.dapatDikirim = (next.dapatDikirim || "all").trim() || "all";
  next.status = normalizeDeliveryScheduleStatus(next.status);
  // Migrate legacy separate PO/customer session values into combined q when possible.
  if (!next.q.trim()) {
    const poOnly = next.po.trim();
    const customerOnly = next.customer.trim();
    if (poOnly && !customerOnly) {
      next.q = poOnly;
      next.po = "";
    } else if (customerOnly && !poOnly) {
      next.q = customerOnly;
      next.customer = "";
    } else if (poOnly && customerOnly && poOnly === customerOnly) {
      next.q = poOnly;
      next.po = "";
      next.customer = "";
    }
  }
  let from = dateKey(next.dateFrom);
  let to = dateKey(next.dateTo);
  if (from && to && from > to) {
    const swap = from;
    from = to;
    to = swap;
  }
  next.dateFrom = from;
  next.dateTo = to;
  return next;
}

export const EMPTY_DELIVERY_SORT = EMPTY_SHIP_SORT;

export const DELIVERY_SORT_KEYS = {
  customer: "customer",
  po_number: "po_number",
  effective_delivery_date: "effective_delivery_date",
  deadline: "deadline",
  jadwal_manual: "jadwal_manual",
  status: "status",
  location_status: "location_status",
  driver: "driver",
  shipped: "shipped",
  shippable: "shippable",
};

export function cycleDeliverySort(current, key) {
  return cycleShipSort(current, key);
}

export function sortDeliveryRows(rows, sort = EMPTY_DELIVERY_SORT) {
  if (sort?.key === DELIVERY_SORT_KEYS.location_status && sort?.direction) {
    const list = Array.isArray(rows) ? rows : [];
    const next = list.slice();
    const direction = sort.direction;
    next.sort((a, b) => {
      const va = locationSetupSortValue(a);
      const vb = locationSetupSortValue(b);
      if (va === vb) return 0;
      const cmp = va < vb ? -1 : 1;
      return direction === "desc" ? -cmp : cmp;
    });
    return next;
  }
  return sortShipRows(rows, sort);
}

/** Count meaningful advanced filter groups (not matching row count). */
export function countActiveDeliveryFilters(filters = {}) {
  const f = { ...EMPTY_DELIVERY_FILTERS, ...(filters || {}) };
  let n = 0;
  if ((f.q || "").trim()) n += 1;
  if ((f.customer || "").trim()) n += 1;
  if ((f.po || "").trim()) n += 1;
  if ((f.dateFrom || "").trim() || (f.dateTo || "").trim()) n += 1;
  if ((f.status || "semua") !== "semua") n += 1;
  if ((f.driver || "").trim()) n += 1;
  if ((f.terkirim || "all") !== "all") n += 1;
  if ((f.dapatDikirim || "all") !== "all") n += 1;
  return n;
}

/** True when draft toolbar/advanced bag differs from applied (normalized). */
export function deliveryScheduleFiltersEqual(a = {}, b = {}) {
  const left = normalizeDeliveryScheduleFilters(a);
  const right = normalizeDeliveryScheduleFilters(b);
  const keys = Object.keys(EMPTY_DELIVERY_FILTERS);
  return keys.every((k) => String(left[k] ?? "") === String(right[k] ?? ""));
}

/** Active Advanced Table column filters — counted separately from toolbar/advanced. */
export function countActiveColumnFilters(colFilters = {}) {
  if (!colFilters || typeof colFilters !== "object") return 0;
  let n = 0;
  for (const conf of Object.values(colFilters)) {
    if (!conf || conf.value == null) continue;
    if (String(conf.value).trim() === "") continue;
    n += 1;
  }
  return n;
}

// Re-export for tests that assert shared ship helpers remain available.
export { countActiveShipFilters, shipDeliveredPercent, shipReadyPercent };

export function rescheduleConfirmLabel(canSetDate) {
  return canSetDate ? "Konfirmasi Jadwal Ulang" : "Konfirmasi Permintaan";
}

/** Stable selection key for SJ-level schedule rows. Prefer shipment_id; never collide multi-SJ on order.id. */
export function scheduleRowSelectionKey(row) {
  const sid = String(row?.shipment_id || "").trim();
  if (sid) return sid;
  const oid = String(row?.id || "").trim();
  return oid;
}

export function scheduleVisibleSelectionKeys(rows) {
  return (rows || []).map((r) => scheduleRowSelectionKey(r)).filter(Boolean);
}

/** Unique order ids for Optimasi Rute from selected SJ keys — algorithm still order-id based. */
export function orderIdsForSelectedScheduleKeys(rows, selectedKeys) {
  const selected = selectedKeys instanceof Set ? selectedKeys : new Set(selectedKeys || []);
  const out = [];
  const seen = new Set();
  for (const row of rows || []) {
    const key = scheduleRowSelectionKey(row);
    if (!key || !selected.has(key)) continue;
    const oid = String(row?.id || "").trim();
    if (!oid || seen.has(oid)) continue;
    seen.add(oid);
    out.push(oid);
  }
  return out;
}

/** Authoritative SJ qty parts: prefer sj_item_lines (per unit), else qty_sj. Never invent unit. */
export function scheduleRowQtyParts(row) {
  const lines = Array.isArray(row?.sj_item_lines) ? row.sj_item_lines : [];
  if (lines.length) {
    const byUnit = new Map();
    let any = false;
    for (const ln of lines) {
      if (!ln || typeof ln !== "object") continue;
      const raw = ln.qty != null ? ln.qty : ln.quantity;
      const qty = Number(raw);
      if (!Number.isFinite(qty)) continue;
      any = true;
      const unit = String(ln.unit || ln.satuan || "").trim();
      byUnit.set(unit, (byUnit.get(unit) || 0) + Math.max(0, qty));
    }
    if (any) {
      return Array.from(byUnit.entries()).map(([unit, qty]) => ({ unit, qty }));
    }
  }
  const q = Number(row?.qty_sj);
  if (Number.isFinite(q)) return [{ unit: "", qty: Math.max(0, q) }];
  return [{ unit: "", qty: 0 }];
}

export function formatScheduleQty(qty, unit = "") {
  const n = Number(qty);
  const num = Number.isFinite(n) ? Math.max(0, n) : 0;
  const formatted = num.toLocaleString("id-ID");
  const u = String(unit || "").trim();
  return u ? `${formatted} ${u}` : formatted;
}

/** Long Indonesian date from YYYY-MM-DD (UTC noon parse — host-TZ safe). */
export function formatDeliveryDetailDate(raw) {
  const key = dateKey(raw);
  if (!key) return "—";
  const [y, m, d] = key.split("-").map(Number);
  if (!y || !m || !d) return "—";
  const dt = new Date(Date.UTC(y, m - 1, d, 12));
  return dt.toLocaleDateString("id-ID", {
    day: "numeric",
    month: "long",
    year: "numeric",
    timeZone: "UTC",
  });
}

/**
 * Build Rincian Pengiriman from fully filtered SJ rows (not a page slice).
 * Groups by effective delivery date; one detail row per SJ; totals by unit.
 */
export function buildDeliveryDetailsSummary(rows, opts = {}) {
  const list = Array.isArray(rows) ? rows : [];
  const dateFrom = dateKey(opts.dateFrom);
  const dateTo = dateKey(opts.dateTo);
  const periodLabel = (() => {
    if (dateFrom && dateTo && dateFrom === dateTo) return formatDeliveryDetailDate(dateFrom);
    if (dateFrom && dateTo) {
      return `${formatDeliveryDetailDate(dateFrom)} – ${formatDeliveryDetailDate(dateTo)}`;
    }
    if (dateFrom) return `Dari ${formatDeliveryDetailDate(dateFrom)}`;
    if (dateTo) return `Sampai ${formatDeliveryDetailDate(dateTo)}`;
    return "Semua tanggal";
  })();

  const byDate = new Map();
  const totalsByUnit = new Map();
  let sjCount = 0;

  for (const row of list) {
    const date = dateKey(row?.effective_delivery_date || row?.sj_delivery_date || row?.tanggal_pengiriman);
    const groupKey = date || "";
    if (!byDate.has(groupKey)) {
      byDate.set(groupKey, {
        date: groupKey,
        dateLabel: date ? formatDeliveryDetailDate(date) : "Tanpa tanggal",
        rows: [],
        totalsByUnit: new Map(),
      });
    }
    const group = byDate.get(groupKey);
    const parts = scheduleRowQtyParts(row);
    const qtyPrimary = parts.reduce((s, p) => s + (Number(p.qty) || 0), 0);
    const unitLabel = parts.length === 1 ? parts[0].unit : (parts.some((p) => p.unit) ? "campuran" : "");
    for (const part of parts) {
      const u = part.unit || "";
      group.totalsByUnit.set(u, (group.totalsByUnit.get(u) || 0) + part.qty);
      totalsByUnit.set(u, (totalsByUnit.get(u) || 0) + part.qty);
    }
    const sjNumber = String(row?.sj_label || row?.latest_sj_number || (row?.sj_numbers && row.sj_numbers[0]) || "").trim();
    group.rows.push({
      key: scheduleRowSelectionKey(row) || `${row?.id || ""}:${sjNumber}:${date}`,
      date,
      sjNumber: sjNumber || "—",
      orderNumber: String(row?.order_number || "").trim() || "—",
      customerName: String(row?.customer_name || "").trim() || "—",
      qty: qtyPrimary,
      qtyLabel: parts.length === 1
        ? formatScheduleQty(parts[0].qty, parts[0].unit)
        : parts.map((p) => formatScheduleQty(p.qty, p.unit)).join(" + "),
      unit: unitLabel,
      status: String(row?.status_label || row?.status || "").trim() || "—",
      parts,
    });
    sjCount += 1;
  }

  const days = Array.from(byDate.values())
    .sort((a, b) => String(a.date).localeCompare(String(b.date)))
    .map((g) => ({
      date: g.date,
      dateLabel: g.dateLabel,
      rows: g.rows,
      totalsByUnit: Array.from(g.totalsByUnit.entries()).map(([unit, qty]) => ({ unit, qty })),
      totalLabel: Array.from(g.totalsByUnit.entries())
        .map(([unit, qty]) => formatScheduleQty(qty, unit))
        .join(" + ") || formatScheduleQty(0, ""),
    }));

  const grandTotals = Array.from(totalsByUnit.entries()).map(([unit, qty]) => ({ unit, qty }));
  return {
    periodLabel,
    sjCount,
    days,
    grandTotals,
    grandTotalLabel: grandTotals.length
      ? grandTotals.map((t) => formatScheduleQty(t.qty, t.unit)).join(" + ")
      : formatScheduleQty(0, ""),
    empty: sjCount === 0,
  };
}
