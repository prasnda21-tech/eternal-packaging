/** Shared PWA task-summary predicates. Counts and visible list must use these. */

import { wibDateKey } from "./suratJalan";

export const TASK_SUMMARY_FILTERS = [
  { id: "hariIni", label: "Tugas Hari Ini" },
  { id: "aktif", label: "Aktif" },
  { id: "proses", label: "Proses" },
  { id: "menunggu", label: "Menunggu" },
];

export const DEFAULT_TASK_SUMMARY_FILTER = "hariIni";

/**
 * Calendar key for a stored task/schedule value.
 * Date-only YYYY-MM-DD is the scheduled calendar day (not shifted).
 * Instant / Date values use Asia/Jakarta day boundaries via wibDateKey.
 */
export function taskCalendarKey(value) {
  if (value instanceof Date) {
    if (Number.isNaN(value.getTime())) return null;
    return wibDateKey(value);
  }
  if (value === null || value === undefined) return null;
  const text = String(value).trim();
  if (!text) return null;
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  if (/^\d{4}-\d{2}-\d{2}[T\s]/.test(text)) {
    const parsed = new Date(text);
    if (!Number.isNaN(parsed.getTime())) return wibDateKey(parsed);
    return text.slice(0, 10);
  }
  const parsed = new Date(text);
  if (Number.isNaN(parsed.getTime())) return null;
  return wibDateKey(parsed);
}

/** Canonical activation/schedule date: work_date (backend _canonical_work_date). */
export function taskWorkDateKey(task) {
  return taskCalendarKey(task?.work_date ?? task?.work_date_key);
}

export function taskTodayKey(now = new Date()) {
  if (typeof now === "string") {
    const key = taskCalendarKey(now);
    if (key) return key;
  }
  const instant = now instanceof Date ? now : new Date(now);
  if (Number.isNaN(instant.getTime())) return wibDateKey(new Date());
  return wibDateKey(instant);
}

/** Canonical completion: division_tasks status "selesai". */
export function isTaskComplete(task) {
  return String(task?.status || "") === "selesai";
}

/**
 * Worked qty when the field exists and is numeric.
 * Missing / blank is not coerced to 0.
 */
export function taskProgressQty(task) {
  const raw = task?.completed_qty;
  if (raw === null || raw === undefined || String(raw).trim() === "") return null;
  const n = Number(raw);
  return Number.isFinite(n) ? n : null;
}

export function isTodayTask(task, now = new Date()) {
  const key = taskWorkDateKey(task);
  if (!key) return false;
  return key === taskTodayKey(now);
}

/** Scheduled on or before today WIB. Future and unscheduled are out of all workload filters. */
export function isTaskInCurrentWorkloadWindow(task, now = new Date()) {
  const key = taskWorkDateKey(task);
  if (!key) return false;
  return key <= taskTodayKey(now);
}

/** Today's unfinished + previous-date unfinished. Future and unscheduled excluded. */
export function isActiveTask(task, now = new Date()) {
  if (!task || isTaskComplete(task)) return false;
  return isTaskInCurrentWorkloadWindow(task, now);
}

/** Canonical partial-progress evidence only. Date window is applied by isInProgressTask. */
export function hasTaskPartialProgress(task) {
  if (String(task?.status || "") === "sebagian") return true;
  const qty = taskProgressQty(task);
  return qty != null && qty > 0;
}

/** Started / partial inside the current workload window. */
export function isInProgressTask(task, now = new Date()) {
  if (!task || isTaskComplete(task)) return false;
  if (!isTaskInCurrentWorkloadWindow(task, now)) return false;
  return hasTaskPartialProgress(task);
}

/** Not finished, in the current workload window, and no progress evidence yet. */
export function isWaitingTask(task, now = new Date()) {
  if (!task || isTaskComplete(task)) return false;
  if (!isTaskInCurrentWorkloadWindow(task, now)) return false;
  return !hasTaskPartialProgress(task);
}

export function matchesTaskSummaryFilter(task, filterId, now = new Date()) {
  switch (filterId) {
    case "hariIni":
      return isTodayTask(task, now);
    case "aktif":
      return isActiveTask(task, now);
    case "proses":
      return isInProgressTask(task, now);
    case "menunggu":
      return isWaitingTask(task, now);
    default:
      return isTodayTask(task, now);
  }
}

export function filterTasksBySummary(tasks, filterId, now = new Date()) {
  const list = Array.isArray(tasks) ? tasks : [];
  return list.filter((task) => matchesTaskSummaryFilter(task, filterId, now));
}

export function taskSummaryFromList(tasks, now = new Date()) {
  const list = Array.isArray(tasks) ? tasks : [];
  return {
    hariIni: list.filter((task) => isTodayTask(task, now)).length,
    aktif: list.filter((task) => isActiveTask(task, now)).length,
    proses: list.filter((task) => isInProgressTask(task, now)).length,
    menunggu: list.filter((task) => isWaitingTask(task, now)).length,
  };
}
