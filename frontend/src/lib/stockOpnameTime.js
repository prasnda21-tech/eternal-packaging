/** Asia/Jakarta (WIB, UTC+7) helpers for Stock Opname physical_count_at. */

const WIB_OFFSET_MS = 7 * 60 * 60 * 1000;

/**
 * Convert API ISO timestamp to datetime-local value in WIB (YYYY-MM-DDTHH:mm).
 * Empty/invalid → "".
 */
export function isoToWibLocalInput(iso) {
  if (iso === null || iso === undefined || iso === "") return "";
  const parsed = new Date(String(iso));
  if (Number.isNaN(parsed.getTime())) return "";
  const wib = new Date(parsed.getTime() + WIB_OFFSET_MS);
  const y = wib.getUTCFullYear();
  const m = String(wib.getUTCMonth() + 1).padStart(2, "0");
  const d = String(wib.getUTCDate()).padStart(2, "0");
  const hh = String(wib.getUTCHours()).padStart(2, "0");
  const mm = String(wib.getUTCMinutes()).padStart(2, "0");
  return `${y}-${m}-${d}T${hh}:${mm}`;
}

/**
 * Convert datetime-local (interpreted as WIB wall clock) to UTC ISO for the API.
 * Blank → null. Does not invent a time from import stamps.
 */
export function wibLocalInputToIso(local) {
  const text = String(local || "").trim();
  if (!text) return null;
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  const hour = Number(match[4]);
  const minute = Number(match[5]);
  const second = Number(match[6] || 0);
  const asUtcMs = Date.UTC(year, month - 1, day, hour, minute, second);
  return new Date(asUtcMs - WIB_OFFSET_MS).toISOString();
}

/** Display ISO in WIB for read-only labels (DD/MM/YYYY HH:mm). */
export function formatWibDateTime(iso) {
  const local = isoToWibLocalInput(iso);
  if (!local) return "—";
  const [date, time] = local.split("T");
  const [y, m, d] = date.split("-");
  return `${d}/${m}/${y} ${time} WIB`;
}

/** Split datetime-local WIB into { date: YYYY-MM-DD, time: HH:mm }. */
export function splitWibLocalInput(local) {
  const text = String(local || "").trim();
  const match = text.match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/);
  if (!match) return { date: "", time: "" };
  return { date: match[1], time: match[2] };
}

/** Combine type=date + type=time (WIB wall) into UTC ISO. */
export function combineWibDateAndTime(dateStr, timeStr) {
  const d = String(dateStr || "").trim();
  const t = String(timeStr || "").trim();
  if (!d || !t) return null;
  return wibLocalInputToIso(`${d}T${t.length === 5 ? t : t.slice(0, 5)}`);
}

/** Format Owner count period for summary/history; missing start → no invention. */
export function formatWibPeriod(startIso, endIso) {
  const start = formatWibDateTime(startIso);
  const end = formatWibDateTime(endIso);
  if (start === "—" && end === "—") return "—";
  if (start === "—") return `s/d ${end}`;
  return `${start} – ${end}`;
}

export const STOCK_OPNAME_TZ_LABEL = "WIB (UTC+7)";
