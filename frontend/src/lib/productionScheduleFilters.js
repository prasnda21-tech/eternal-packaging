/** Filter/sort pipeline used by ProductionSchedule page (runtime + tests). */

export const FILTER_DEFAULTS = {
  q: "",
  poFilter: "",
  statusFilter: "all",
  operatorFilter: "all",
  customerFilter: "all",
  urgentOnly: false,
  holdOnly: false,
  screenerFilter: "all",
  helperFilter: "all",
  packingFilter: "all",
  deliveryFilter: "all",
  bufferFilter: "all",
  dateFrom: "",
  dateTo: "",
  workDateFilter: "all",
  workDateFrom: "",
  workDateTo: "",
  sortBy: "deadline",
  includeCancelled: false,
  includeCompleted: true,
};

export function statusOfRow(r) {
  if (r.cancelled) return "cancelled";
  if ((r.current_stage || 0) >= 14) return "selesai";
  if (r.hold) return "hold";
  return "aktif";
}

export function screenerFilled(r) {
  const v = r.screen_qty;
  return v !== null && v !== undefined && String(v).trim() !== "";
}

export function normalizeDeliveryStatus(r) {
  const raw = r.delivery_status;
  if (!raw || raw === "Belum" || raw === "Belum Terkirim") return "Belum Terkirim";
  if (raw === "Sebagian" || raw === "Sebagian Terkirim") return "Sebagian Terkirim";
  if (raw === "Terkirim") return "Terkirim";
  return raw;
}

/** Normalize ISO / Date / YYYY-MM-DD to a comparable YYYY-MM-DD key (local date for Date objects). */
export function toDateKey(value) {
  if (value === null || value === undefined || value === "") return null;
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return null;
    const y = value.getFullYear();
    const m = String(value.getMonth() + 1).padStart(2, "0");
    const d = String(value.getDate()).padStart(2, "0");
    return `${y}-${m}-${d}`;
  }
  const s = String(value).trim();
  const isoDay = s.match(/^(\d{4}-\d{2}-\d{2})/);
  if (isoDay) return isoDay[1];
  const parsed = new Date(s);
  if (Number.isNaN(parsed.getTime())) return null;
  return toDateKey(parsed);
}

export function workDateKeys(r) {
  const keys = [];
  const own = toDateKey(r.work_date);
  if (own) keys.push(own);
  for (const o of r.operator_work_dates || []) {
    const k = toDateKey(o?.work_date);
    if (k) keys.push(k);
  }
  return keys;
}

export function isScheduledToday(r, today = new Date()) {
  if (!r) return false;
  if (r.cancelled || r.status_final === "Cancelled") return false;
  if ((r.current_stage || 0) >= 14 || r.status_final === "Terkirim") return false;
  const status = statusOfRow(r);
  if (status === "cancelled" || status === "selesai") return false;
  const todayKey = toDateKey(today);
  if (!todayKey) return false;
  return workDateKeys(r).includes(todayKey);
}


export function applyProductionFilters(rows, filters = {}) {
  const f = { ...FILTER_DEFAULTS, ...filters };
  const q = String(f.q || "").trim().toLowerCase();

  return (rows || []).filter((r) => {
    if (!f.includeCancelled && r.cancelled) return false;
    if (!f.includeCompleted && !r.cancelled && (r.current_stage || 0) >= 14) return false;

    if (f.statusFilter !== "all" && statusOfRow(r) !== f.statusFilter) return false;
    if (f.urgentOnly && !r.urgent) return false;
    if (f.holdOnly && !r.hold) return false;
    if (f.customerFilter !== "all" && r.customer !== f.customerFilter) return false;
    if (f.poFilter) {
      const needle = String(f.poFilter).trim().toLowerCase();
      if (needle && !(r.po_number || "").toLowerCase().includes(needle)) return false;
    }
    if (f.operatorFilter !== "all") {
      const ops = (r.operators || []).map((o) => o.operator_name);
      if (!ops.includes(f.operatorFilter)) return false;
    }
    if (f.screenerFilter === "yes" && !screenerFilled(r)) return false;
    if (f.screenerFilter === "no" && screenerFilled(r)) return false;
    if (f.helperFilter === "ok" && r.helper_shortage_status !== "Cukup") return false;
    if (f.helperFilter === "kurang" && r.helper_shortage_status !== "Barang Kurang") return false;
    if (f.helperFilter === "none") {
      const req = r.helper_required;
      if (!(req === null || req === undefined || req === "")) return false;
    }
    const packingStatus = r.packing_status || "Belum";
    if (f.packingFilter !== "all" && packingStatus !== f.packingFilter) return false;
    const deliveryStatus = normalizeDeliveryStatus(r);
    if (f.deliveryFilter !== "all" && deliveryStatus !== f.deliveryFilter) return false;
    if (f.bufferFilter === "yes" && !r.buffer_stock) return false;
    if (f.bufferFilter === "no" && !!r.buffer_stock) return false;

    const deadlineKey = toDateKey(r.deadline);
    if (f.dateFrom) {
      const from = toDateKey(f.dateFrom);
      if (!deadlineKey || !from || deadlineKey < from) return false;
    }
    if (f.dateTo) {
      const to = toDateKey(f.dateTo);
      if (!deadlineKey || !to || deadlineKey > to) return false;
    }

    const wkeys = workDateKeys(r);
    const scheduled = wkeys.length > 0;
    if (f.workDateFilter === "scheduled" && !scheduled) return false;
    if (f.workDateFilter === "unscheduled" && scheduled) return false;
    if (f.workDateFrom || f.workDateTo) {
      if (!scheduled) return false;
      const from = f.workDateFrom ? toDateKey(f.workDateFrom) : null;
      const to = f.workDateTo ? toDateKey(f.workDateTo) : null;
      const inRange = wkeys.some((k) => (!from || k >= from) && (!to || k <= to));
      if (!inRange) return false;
    }

    if (q) {
      const hay = `${r.po_number || ""} ${r.customer || ""} ${r.outlet || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

export function sortProductionRows(rows, sortBy = "deadline") {
  const arr = [...(rows || [])];
  arr.sort((a, b) => {
    if (sortBy === "work_date" || sortBy === "work_date_asc") {
      const aw = toDateKey(a.work_date) || workDateKeys(a)[0] || "9999-12-31";
      const bw = toDateKey(b.work_date) || workDateKeys(b)[0] || "9999-12-31";
      return sortBy === "work_date_asc" ? aw.localeCompare(bw) : bw.localeCompare(aw);
    }
    const ad = toDateKey(a.deadline) || "9999-12-31";
    const bd = toDateKey(b.deadline) || "9999-12-31";
    return ad.localeCompare(bd);
  });
  return arr;
}

export function productionFiltersAreActive(filters = {}) {
  const f = { ...FILTER_DEFAULTS, ...filters };
  return Boolean(
    String(f.q || "").trim()
    || String(f.poFilter || "").trim()
    || f.statusFilter !== FILTER_DEFAULTS.statusFilter
    || f.operatorFilter !== FILTER_DEFAULTS.operatorFilter
    || f.customerFilter !== FILTER_DEFAULTS.customerFilter
    || f.urgentOnly
    || f.holdOnly
    || f.screenerFilter !== FILTER_DEFAULTS.screenerFilter
    || f.helperFilter !== FILTER_DEFAULTS.helperFilter
    || f.packingFilter !== FILTER_DEFAULTS.packingFilter
    || f.deliveryFilter !== FILTER_DEFAULTS.deliveryFilter
    || f.bufferFilter !== FILTER_DEFAULTS.bufferFilter
    || f.dateFrom
    || f.dateTo
    || f.workDateFilter !== FILTER_DEFAULTS.workDateFilter
    || f.workDateFrom
    || f.workDateTo
    || f.includeCancelled !== FILTER_DEFAULTS.includeCancelled
    || f.includeCompleted !== FILTER_DEFAULTS.includeCompleted
  );
}
