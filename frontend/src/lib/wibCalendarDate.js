/**
 * Calendar-day helpers in Asia/Jakarta (WIB, UTC+7).
 * Emits YYYY-MM-DD only — safe for Eternal sales create payloads that store
 * date-only (or parse YYYY-MM-DD without shifting the calendar day).
 */

const WIB_TZ = "Asia/Jakarta";

function pad2(n) {
  return String(n).padStart(2, "0");
}

/** Today's calendar date in Asia/Jakarta as YYYY-MM-DD. */
export function getWibTodayString(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: WIB_TZ,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now instanceof Date ? now : new Date(now));
  const map = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  return `${map.year}-${map.month}-${map.day}`;
}

/**
 * Add whole calendar days to a YYYY-MM-DD (or today WIB when empty).
 * Arithmetic is on the calendar Y-M-D parts — no UTC midnight shift.
 */
export function addWibCalendarDays(isoOrEmpty, days, now = new Date()) {
  const base = String(isoOrEmpty || "").trim() || getWibTodayString(now);
  const match = base.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return "";
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  // Noon UTC avoids DST edge cases; we only read Y/M/D after add.
  const utc = Date.UTC(year, month - 1, day, 12, 0, 0);
  const shifted = new Date(utc + Number(days || 0) * 86400000);
  return `${shifted.getUTCFullYear()}-${pad2(shifted.getUTCMonth() + 1)}-${pad2(shifted.getUTCDate())}`;
}

/** Convenience: today WIB + N calendar days. */
export function getWibTodayPlusDays(days, now = new Date()) {
  return addWibCalendarDays(getWibTodayString(now), days, now);
}
