/**
 * Shared WIB date-only presets for sales list header filters.
 * Shipping presets: Pesanan Tanggal Kirim + SJ Tgl Kirim.
 * Invoice presets: Faktur Tanggal (issue date).
 */

export const SHIPPING_DATE_PRESET_OPTIONS = [
  { value: "all", label: "Semua" },
  { value: "today", label: "Hari ini" },
  { value: "tomorrow", label: "Besok" },
  { value: "next_3", label: "3 hari ke depan" },
  { value: "next_7", label: "7 hari ke depan" },
  { value: "this_week", label: "Minggu ini" },
  { value: "next_week", label: "Minggu depan" },
  { value: "overdue", label: "Terlambat" },
  { value: "unset", label: "Belum ada tanggal" },
];

export const INVOICE_DATE_PRESET_OPTIONS = [
  { value: "all", label: "Semua" },
  { value: "today", label: "Hari ini" },
  { value: "this_week", label: "Minggu ini" },
  { value: "this_month", label: "Bulan ini" },
  { value: "last_month", label: "Bulan lalu" },
];

/** Asia/Jakarta (WIB, UTC+7) calendar date YYYY-MM-DD. */
export function wibTodayKey(now = new Date()) {
  const instant = now instanceof Date ? now : new Date(now);
  const wib = new Date(instant.getTime() + 7 * 60 * 60 * 1000);
  const y = wib.getUTCFullYear();
  const m = String(wib.getUTCMonth() + 1).padStart(2, "0");
  const d = String(wib.getUTCDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

function addDays(isoDay, days) {
  const [y, m, d] = isoDay.split("-").map(Number);
  const utc = Date.UTC(y, m - 1, d) + days * 86400000;
  const dt = new Date(utc);
  const yy = dt.getUTCFullYear();
  const mm = String(dt.getUTCMonth() + 1).padStart(2, "0");
  const dd = String(dt.getUTCDate()).padStart(2, "0");
  return `${yy}-${mm}-${dd}`;
}

/** Monday–Sunday week bounds for a WIB calendar day. */
export function wibWeekBounds(isoDay) {
  const [y, m, d] = isoDay.split("-").map(Number);
  const utc = Date.UTC(y, m - 1, d);
  const weekday = new Date(utc).getUTCDay(); // 0 Sun … 6 Sat
  const mondayOffset = weekday === 0 ? -6 : 1 - weekday;
  const monday = addDays(isoDay, mondayOffset);
  const sunday = addDays(monday, 6);
  return { from: monday, to: sunday };
}

/**
 * Shipping date preset → filter descriptor.
 * kind: all | range | overdue | unset
 */
export function shippingDatePresetBounds(preset, today = wibTodayKey()) {
  const key = String(preset || "all").trim() || "all";
  if (key === "all") return { kind: "all" };
  if (key === "unset") return { kind: "unset" };
  if (key === "overdue") return { kind: "overdue", before: today };
  if (key === "today") return { kind: "range", from: today, to: today };
  if (key === "tomorrow") {
    const day = addDays(today, 1);
    return { kind: "range", from: day, to: day };
  }
  if (key === "next_3") return { kind: "range", from: today, to: addDays(today, 2) };
  if (key === "next_7") return { kind: "range", from: today, to: addDays(today, 6) };
  if (key === "this_week") {
    const week = wibWeekBounds(today);
    return { kind: "range", from: week.from, to: week.to };
  }
  if (key === "next_week") {
    const week = wibWeekBounds(addDays(today, 7));
    return { kind: "range", from: week.from, to: week.to };
  }
  return { kind: "all" };
}

/** Invoice issue-date preset → date_from / date_to (empty when all). */
export function invoiceDatePresetRange(preset, today = wibTodayKey()) {
  const key = String(preset || "all").trim() || "all";
  if (key === "all") return { date_from: "", date_to: "" };
  if (key === "today") return { date_from: today, date_to: today };
  if (key === "this_week") {
    const week = wibWeekBounds(today);
    return { date_from: week.from, date_to: week.to };
  }
  if (key === "this_month") {
    const [y, m] = today.split("-").map(Number);
    const from = `${y}-${String(m).padStart(2, "0")}-01`;
    const nextY = m === 12 ? y + 1 : y;
    const nextM = m === 12 ? 1 : m + 1;
    const to = addDays(`${nextY}-${String(nextM).padStart(2, "0")}-01`, -1);
    return { date_from: from, date_to: to };
  }
  if (key === "last_month") {
    const [y, m] = today.split("-").map(Number);
    const prevY = m === 1 ? y - 1 : y;
    const prevM = m === 1 ? 12 : m - 1;
    const from = `${prevY}-${String(prevM).padStart(2, "0")}-01`;
    const to = addDays(`${y}-${String(m).padStart(2, "0")}-01`, -1);
    return { date_from: from, date_to: to };
  }
  return { date_from: "", date_to: "" };
}

export function shippingDatePresetActive(preset) {
  const key = String(preset || "all").trim() || "all";
  return key !== "all";
}
