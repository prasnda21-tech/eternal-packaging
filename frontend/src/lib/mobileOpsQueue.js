/** Shared PWA operational queue filters + badges (production + delivery). */

import { formatIdQty } from "./productionScheduleQty";
import { toDateKey, workDateKeys, isScheduledToday, statusOfRow } from "./productionScheduleFilters";
import { dateKey } from "./deliverySchedule";
import { sjItemLinesFromRow } from "./mobileDeliverySchedule";

export const OPS_QUICK_FILTERS = [
  { key: "semua", label: "Semua" },
  { key: "hari_ini", label: "Hari Ini" },
  { key: "terlambat", label: "Terlambat" },
  { key: "besok", label: "Besok" },
  { key: "belum_dijadwalkan", label: "Belum Dijadwalkan" },
  { key: "minggu_ini", label: "Minggu Ini" },
  { key: "semua_aktif", label: "Semua Aktif" },
];

export const OPS_TOOLBAR_FILTERS = OPS_QUICK_FILTERS.filter((f) => (
  f.key === "semua" || f.key === "hari_ini" || f.key === "terlambat" || f.key === "besok"
));
export const OPS_MORE_FILTERS = OPS_QUICK_FILTERS.filter((f) => (
  f.key === "belum_dijadwalkan" || f.key === "minggu_ini" || f.key === "semua_aktif"
));

function addDays(isoDay, days) {
  const d = new Date(`${isoDay}T12:00:00`);
  d.setDate(d.getDate() + days);
  return toDateKey(d);
}

function weekBounds(todayKey) {
  const d = new Date(`${todayKey}T12:00:00`);
  const day = d.getDay(); // 0 Sun
  const mondayOffset = day === 0 ? -6 : 1 - day;
  const start = addDays(todayKey, mondayOffset);
  const end = addDays(start, 6);
  return { start, end };
}

export function opsTodayKey(now = new Date()) {
  return toDateKey(now);
}

/** Production row operational date keys (work_date + operator work dates). */
export function productionOpsDateKeys(row) {
  return workDateKeys(row);
}

export function productionQuickMatch(row, quickKey, today = new Date()) {
  const todayKey = opsTodayKey(today);
  const tomorrow = addDays(todayKey, 1);
  const keys = productionOpsDateKeys(row);
  const st = statusOfRow(row);
  const finished = st === "selesai" || st === "cancelled" || row?.cancelled || (row?.current_stage || 0) >= 14;

  if (quickKey === "semua") return true;
  if (quickKey === "semua_aktif") return !finished;
  if (finished) return false;

  if (quickKey === "hari_ini") return isScheduledToday(row, today);
  if (quickKey === "besok") return keys.includes(tomorrow);
  if (quickKey === "belum_dijadwalkan") return keys.length === 0;
  if (quickKey === "terlambat") {
    if (!keys.length) return false;
    const latest = keys.slice().sort().pop();
    return latest < todayKey && !finished;
  }
  if (quickKey === "minggu_ini") {
    const { start, end } = weekBounds(todayKey);
    return keys.some((k) => k >= start && k <= end);
  }
  return true;
}

/** Delivery schedule status / date semantics aligned with Web DELIVERY_SCHEDULE_STATUS_OPTIONS. */
export function deliveryQuickMatch(row, quickKey, today = new Date()) {
  const todayKey = opsTodayKey(today);
  const tomorrow = addDays(todayKey, 1);
  const eff = dateKey(row?.effective_delivery_date);
  const status = String(row?.status || "");
  const finished = status === "terkirim" || String(row?.delivery_status || "").toLowerCase() === "terkirim";

  if (quickKey === "semua") return true;
  if (quickKey === "semua_aktif") return !finished;
  if (finished) return false;

  if (quickKey === "hari_ini") return status === "hari_ini" || eff === todayKey;
  if (quickKey === "besok") return eff === tomorrow;
  if (quickKey === "terlambat") return status === "terlambat" || (eff && eff < todayKey && !finished);
  if (quickKey === "belum_dijadwalkan") return status === "belum_dijadwalkan" || !eff;
  if (quickKey === "minggu_ini") {
    if (!eff) return false;
    const { start, end } = weekBounds(todayKey);
    return eff >= start && eff <= end;
  }
  return true;
}

/**
 * Canonical visual queue order:
 * 1 Terlambat 2 Hari Ini 3 Besok 4 next dates 5 Belum Dijadwalkan
 * Urgent (existing order.priority) only boosts within category — no manual priority field.
 */
export function productionQueueRank(row, today = new Date()) {
  const todayKey = opsTodayKey(today);
  const tomorrow = addDays(todayKey, 1);
  const keys = productionOpsDateKeys(row).slice().sort();
  const finished = statusOfRow(row) === "selesai" || row?.cancelled;
  if (finished) return { bucket: 90, date: "9999-99-99", urgent: 1 };
  if (!keys.length) return { bucket: 50, date: "9999-99-99", urgent: row?.urgent ? 0 : 1 };
  const earliest = keys[0];
  if (earliest < todayKey) return { bucket: 10, date: earliest, urgent: row?.urgent ? 0 : 1 };
  if (keys.includes(todayKey)) return { bucket: 20, date: todayKey, urgent: row?.urgent ? 0 : 1 };
  if (keys.includes(tomorrow)) return { bucket: 30, date: tomorrow, urgent: row?.urgent ? 0 : 1 };
  return { bucket: 40, date: earliest, urgent: row?.urgent ? 0 : 1 };
}

export function deliveryQueueRank(row, today = new Date()) {
  const todayKey = opsTodayKey(today);
  const tomorrow = addDays(todayKey, 1);
  const eff = dateKey(row?.effective_delivery_date) || "";
  const status = String(row?.status || "");
  const finished = status === "terkirim";
  if (finished) return { bucket: 90, date: "9999-99-99", urgent: 1 };
  if (status === "terlambat" || (eff && eff < todayKey)) return { bucket: 10, date: eff || "0000-00-00", urgent: 1 };
  if (status === "hari_ini" || eff === todayKey) return { bucket: 20, date: todayKey, urgent: 1 };
  if (eff === tomorrow) return { bucket: 30, date: tomorrow, urgent: 1 };
  if (!eff || status === "belum_dijadwalkan") return { bucket: 50, date: "9999-99-99", urgent: 1 };
  return { bucket: 40, date: eff, urgent: 1 };
}

export function sortByOpsQueue(rows, rankFn, today = new Date()) {
  return (rows || [])
    .map((row, idx) => ({ row, idx, rank: rankFn(row, today) }))
    .sort((a, b) => {
      if (a.rank.bucket !== b.rank.bucket) return a.rank.bucket - b.rank.bucket;
      if (a.rank.urgent !== b.rank.urgent) return a.rank.urgent - b.rank.urgent;
      if (a.rank.date !== b.rank.date) return String(a.rank.date).localeCompare(String(b.rank.date));
      return a.idx - b.idx;
    })
    .map((x) => x.row);
}

export function productionOpsBadge(row, today = new Date()) {
  const todayKey = opsTodayKey(today);
  const tomorrow = addDays(todayKey, 1);
  const keys = productionOpsDateKeys(row).slice().sort();
  const finished = statusOfRow(row) === "selesai" || row?.cancelled;
  const dateBadge = (() => {
    if (!keys.length) return { key: "unscheduled", label: "Belum Dijadwalkan", tone: "gray" };
    const earliest = keys[0];
    if (earliest < todayKey) return { key: "late", label: "Terlambat", tone: "red" };
    if (keys.includes(todayKey)) return { key: "today", label: "Hari ini", tone: "blue" };
    if (keys.includes(tomorrow)) return { key: "tomorrow", label: "Besok", tone: "blue" };
    return null;
  })();
  if (finished) {
    return { key: "done", label: "SELESAI", tone: "green", caption: "Selesai", captionTone: "green" };
  }
  if (row?.urgent) {
    return { key: "urgent", label: "URGENT", tone: "urgent", caption: dateBadge?.label, captionTone: dateBadge?.tone };
  }
  return { key: "normal", label: "NORMAL", tone: "blue", caption: dateBadge?.label, captionTone: dateBadge?.tone };
}

export function deliveryOpsBadge(row, today = new Date()) {
  const todayKey = opsTodayKey(today);
  const tomorrow = addDays(todayKey, 1);
  const eff = dateKey(row?.effective_delivery_date);
  const status = String(row?.status || "");
  const dateBadge = (() => {
    if (status === "terlambat" || (eff && eff < todayKey && status !== "terkirim")) {
      return { key: "late", label: "Terlambat", tone: "red" };
    }
    if (status === "hari_ini" || eff === todayKey) return { key: "today", label: "Hari ini", tone: "blue" };
    if (eff === tomorrow) return { key: "tomorrow", label: "Besok", tone: "blue" };
    if (status === "belum_dijadwalkan" || !eff) return { key: "unscheduled", label: "Belum Dijadwalkan", tone: "gray" };
    return null;
  })();
  if (status === "terkirim") {
    return { key: "done", label: "SELESAI", tone: "green", caption: "Selesai", captionTone: "green" };
  }
  if (row?.urgent === true) {
    return { key: "urgent", label: "URGENT", tone: "urgent", caption: dateBadge?.label, captionTone: dateBadge?.tone };
  }
  return { key: "normal", label: "NORMAL", tone: "blue", caption: dateBadge?.label, captionTone: dateBadge?.tone };
}

export function qtyProgressText(done, target) {
  const d = Number(done);
  const t = Number(target);
  if (!Number.isFinite(t) || t <= 0) {
    if (!Number.isFinite(d) || d <= 0) return "—";
    return formatIdQty(d);
  }
  return `${formatIdQty(Number.isFinite(d) ? d : 0)} / ${formatIdQty(t)}`;
}

export function screenProgressText(row) {
  const done = row?.screen_qty;
  if (done == null || String(done).trim() === "") return "—";
  return formatIdQty(done);
}

export function helperProgressText(row) {
  if (row?.helper_required == null) return "—";
  return qtyProgressText(row.helper_prepared ?? 0, row.helper_required);
}

export function packingProgressText(row) {
  const done = row?.qty_packed ?? row?.packing_qty;
  const target = row?.qty_total ?? row?.total_quantity;
  if (done == null && target == null) return "—";
  return qtyProgressText(done ?? 0, target ?? 0);
}

export function matchesProductionOpsSearch(row, q) {
  const s = String(q || "").trim().toLowerCase();
  if (!s) return true;
  const items = (row?.operators || []).map((op) => op?.line_name || "").join(" ");
  const hay = `${row?.po_number || ""} ${row?.customer || ""} ${row?.outlet || ""} ${items}`.toLowerCase();
  return hay.includes(s);
}

export function matchesDeliveryOpsSearch(row, q) {
  const s = String(q || "").trim().toLowerCase();
  if (!s) return true;
  const items = sjItemLinesFromRow(row).map((ln) => ln?.name || "").join(" ");
  const hay = [
    row?.order_number,
    row?.customer_name,
    row?.latest_sj_number,
    row?.sj_label,
    items,
  ].filter(Boolean).join(" ").toLowerCase();
  return hay.includes(s);
}

export function opsQuickFilterCounts(rows, matchFn, today = new Date()) {
  const out = {};
  for (const f of OPS_QUICK_FILTERS) {
    out[f.key] = (rows || []).filter((r) => matchFn(r, f.key, today)).length;
  }
  return out;
}
