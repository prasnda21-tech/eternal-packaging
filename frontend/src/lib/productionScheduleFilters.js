/** Filter/sort pipeline used by ProductionSchedule page (runtime + tests). */

import {
  buildGroupedDisplayOperator,
  productionScheduleAssignmentKey,
  productionScheduleOperatorIdentity,
  productionScheduleSplitWorkDate,
  toProductionScheduleDisplayRow,
} from "./productionScheduleDisplayRows";
import { isPrintableScheduleSplit, printableCompletedQty } from "./productionScheduleQty";

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

function qtyInt(v) {
  const n = Number(v);
  return Number.isFinite(n) ? n : 0;
}

function assignedPrintableQty(op) {
  if (op?.quantity == null || op.quantity === "") return null;
  const n = Number(op.quantity);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n;
}

function workUnitProgress(splits) {
  let scheduled = 0;
  let hasAssigned = false;
  for (const op of splits || []) {
    if (!isPrintableScheduleSplit(op)) continue;
    const assigned = assignedPrintableQty(op);
    if (assigned == null) continue;
    hasAssigned = true;
    scheduled += assigned;
  }
  if (!hasAssigned) return null;
  return { scheduled, completed: printableCompletedQty(splits) };
}

/** Asia/Jakarta (WIB, UTC+7) calendar date. Same algorithm as `wibDateKey`. */
export function productionTodayKey(now = new Date()) {
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60000;
  const wib = new Date(utcMs + 7 * 3600000);
  const y = wib.getUTCFullYear();
  const m = String(wib.getUTCMonth() + 1).padStart(2, "0");
  const d = String(wib.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function resolveProductionTodayKey(today) {
  if (today instanceof Date) return productionTodayKey(today);
  if (typeof today === "string" && today.trim()) {
    return toDateKey(today) || productionTodayKey();
  }
  return productionTodayKey();
}

/** Visible Tgl Pengerjaan on a display/work row. Date-only strings stay calendar dates. */
export function scheduledWorkDateKey(row) {
  if (!row) return null;
  return toDateKey(row.displayWorkDate) || toDateKey(row.work_date) || null;
}

function sourceSplits(row) {
  const grouped = row?.displayOperator?.splits;
  if (Array.isArray(grouped) && grouped.length) return grouped;
  return Array.isArray(row?.operators) ? row.operators : [];
}

/**
 * Canonical Hari Ini work unit: one operator + one scheduled work date + that
 * date's printable assigned/completed qty. Same operator + two dates = two units.
 */
export function productionScheduleWorkUnits(row) {
  if (!row) return [];
  const cancelled = Boolean(row.cancelled || row.status_final === "Cancelled" || statusOfRow(row) === "cancelled");
  const splits = sourceSplits(row).filter(isPrintableScheduleSplit);
  if (!splits.length) {
    if ((row.operators || []).length) return [];
    const workDate = toDateKey(row.work_date);
    if (!workDate) return [];
    const scheduled = assignedPrintableQty({ quantity: row.print_qty_total })
      ?? assignedPrintableQty({ quantity: row.total_quantity });
    if (scheduled == null) return [];
    const completedRaw = row.print_completed_qty;
    const completed = (completedRaw == null || completedRaw === "") ? 0 : qtyInt(completedRaw);
    return [{
      row,
      operatorKey: "unassigned",
      operator_name: null,
      workDate,
      splits: [],
      scheduled,
      completed,
      cancelled,
    }];
  }

  const buckets = new Map();
  splits.forEach((op, index) => {
    const workDate = toDateKey(productionScheduleSplitWorkDate(row, op) || op.work_date);
    if (!workDate) return;
    const operatorKey = productionScheduleOperatorIdentity(op) || `unknown:${index}`;
    const key = `${operatorKey}::${workDate}`;
    const bucket = buckets.get(key) || {
      operatorKey,
      operator_name: op.operator_name || null,
      workDate,
      splits: [],
    };
    bucket.splits.push(op);
    buckets.set(key, bucket);
  });

  return [...buckets.values()].flatMap((bucket) => {
    const progress = workUnitProgress(bucket.splits);
    if (!progress) return [];
    return [{
      row,
      operatorKey: bucket.operatorKey,
      operator_name: bucket.operator_name,
      workDate: bucket.workDate,
      splits: bucket.splits,
      scheduled: progress.scheduled,
      completed: progress.completed,
      cancelled,
    }];
  }).sort((a, b) => {
    const dateCmp = a.workDate.localeCompare(b.workDate);
    if (dateCmp) return dateCmp;
    return String(a.operator_name || a.operatorKey).localeCompare(String(b.operator_name || b.operatorKey));
  });
}

function visibleWorkSplits(row) {
  const splits = row?.displayOperator?.splits;
  if (!Array.isArray(splits) || !splits.length) return null;
  const visible = scheduledWorkDateKey(row);
  const dated = splits.filter((s) => toDateKey(productionScheduleSplitWorkDate(row, s) || s.work_date));
  if (visible && dated.length) {
    const matching = splits.filter((s) => toDateKey(productionScheduleSplitWorkDate(row, s) || s.work_date) === visible);
    if (matching.length) return matching;
  }
  return splits;
}

/** Scheduled vs completed qty for the visible work row — not PO stage. */
export function scheduledWorkProgress(row) {
  const splits = visibleWorkSplits(row);
  if (splits) {
    return workUnitProgress(splits) || { scheduled: 0, completed: printableCompletedQty(splits) };
  }
  const units = productionScheduleWorkUnits(row);
  if (units.length === 1) return { scheduled: units[0].scheduled, completed: units[0].completed };
  if (row?.displayOperatorKey === "unassigned" || !(row?.operators || []).length) {
    return {
      scheduled: assignedPrintableQty({ quantity: row?.print_qty_total })
        ?? assignedPrintableQty({ quantity: row?.total_quantity })
        ?? 0,
      completed: qtyInt(row?.print_completed_qty),
    };
  }
  return workUnitProgress(row.operators) || { scheduled: 0, completed: printableCompletedQty(row.operators) };
}

export function isScheduledWorkUnfinished(rowOrUnit) {
  const scheduled = rowOrUnit?.scheduled;
  const completed = rowOrUnit?.completed;
  if (typeof scheduled === "number" && typeof completed === "number") {
    return scheduled > 0 && completed < scheduled;
  }
  const progress = scheduledWorkProgress(rowOrUnit);
  return progress.scheduled > 0 && progress.completed < progress.scheduled;
}

export function isWorkScheduledToday(row, today) {
  const key = scheduledWorkDateKey(row);
  const todayKey = resolveProductionTodayKey(today);
  return Boolean(key && todayKey && key === todayKey);
}

export function isHistoricalUnfinishedWork(row, today) {
  return productionScheduleWorkUnits(row).some((unit) => {
    if (unit.cancelled) return false;
    const todayKey = resolveProductionTodayKey(today);
    return unit.workDate < todayKey && isScheduledWorkUnfinished(unit);
  });
}

/**
 * Exact scheduled work unit: operator + work date + that date's printable qty.
 * current_stage is not used.
 */
export function isTodayWorkloadUnit(unit, today, includeCompleted = true) {
  if (!unit?.workDate) return false;
  const todayKey = resolveProductionTodayKey(today);
  if (!todayKey) return false;
  if (unit.workDate > todayKey) return false;
  if (unit.cancelled) return unit.workDate === todayKey;
  const unfinished = isScheduledWorkUnfinished(unit);
  if (unit.workDate < todayKey) return unfinished;
  if (unfinished) return true;
  return Boolean(includeCompleted);
}

/**
 * Desktop Jadwal Produksi "Hari Ini" workload for a row:
 * any exact work unit on the row qualifies. Delivery dates are ignored.
 */
export function isTodayWorkloadRow(row, today, includeCompleted = true) {
  if (!row) return false;
  return productionScheduleWorkUnits(row).some((unit) => isTodayWorkloadUnit(unit, today, includeCompleted));
}

function displayRowsFromWorkUnit(row, unit) {
  const splits = unit.splits.length ? unit.splits : [null];
  return splits.map((split, index) => {
    const list = split ? [split] : [];
    const displayOperator = list.length ? buildGroupedDisplayOperator(list) : null;
    const assignmentKey = split
      ? productionScheduleAssignmentKey(split, index)
      : undefined;
    return toProductionScheduleDisplayRow(row, {
      operatorKey: unit.operatorKey,
      displayOperator,
      displayWorkDate: unit.workDate,
      assignmentKey: assignmentKey || (unit.workDate ? `date:${unit.workDate}` : undefined),
    });
  });
}

export function buildHariIniDisplayRows(rows, today, includeCompleted = true) {
  const out = [];
  for (const row of rows || []) {
    const units = productionScheduleWorkUnits(row).filter((unit) => (
      isTodayWorkloadUnit(unit, today, includeCompleted)
    ));
    for (const unit of units) out.push(...displayRowsFromWorkUnit(row, unit));
  }
  return out;
}

export function applyHariIniDisplayRows(rows, today, includeCompleted = true) {
  return buildHariIniDisplayRows(rows, today, includeCompleted);
}

export function applyWorkDateFilterControl(nextFilter) {
  if (nextFilter === "hari_ini") {
    return { workDateFilter: "hari_ini", workDateFrom: "", workDateTo: "" };
  }
  return { workDateFilter: nextFilter };
}

export function applyWorkDateRangeControl(from, to, currentFilter) {
  if ((from || to) && currentFilter === "hari_ini") {
    return { workDateFilter: "all", workDateFrom: from, workDateTo: to };
  }
  return { workDateFilter: currentFilter, workDateFrom: from, workDateTo: to };
}

/** Calendar-day range for a column date-filter "Hari Ini" shortcut (WIB). */
export function applyDateRangeTodayShortcut(now = new Date()) {
  const today = productionTodayKey(now);
  return { from: today, to: today };
}

export function applyWorkDateTodayShortcut(currentFilter, now = new Date()) {
  const { from, to } = applyDateRangeTodayShortcut(now);
  return applyWorkDateRangeControl(from, to, currentFilter);
}

export function applyDeadlineTodayShortcut(now = new Date()) {
  const { from, to } = applyDateRangeTodayShortcut(now);
  return { dateFrom: from, dateTo: to };
}

function poHasTodayWorkload(row, today, includeCompleted) {
  return productionScheduleWorkUnits(row).some((unit) => isTodayWorkloadUnit(unit, today, includeCompleted));
}

export function applyProductionFilters(rows, filters = {}) {
  const f = { ...FILTER_DEFAULTS, ...filters };
  const q = String(f.q || "").trim().toLowerCase();
  const todayKey = resolveProductionTodayKey(f.today);

  return (rows || []).filter((r) => {
    if (!f.includeCancelled && r.cancelled) return false;
    if (
      f.workDateFilter !== "hari_ini"
      && !f.includeCompleted
      && !r.cancelled
      && (r.current_stage || 0) >= 14
    ) return false;

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

    if (f.workDateFilter === "hari_ini") {
      if (!poHasTodayWorkload(r, todayKey, f.includeCompleted)) return false;
    } else {
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
