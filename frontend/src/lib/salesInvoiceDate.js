/** Sales Invoice dates only. Identify the pattern before reading day and month. */

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_TIMESTAMP = /^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})?$/;
const YMD_SLASH = /^(\d{4})\/(\d{2})\/(\d{2})$/;
const DMY_SLASH = /^(\d{2})\/(\d{2})\/(\d{4})$/;
const DMY_DASH = /^(\d{2})-(\d{2})-(\d{4})$/;

function calendarDate(year, month, day) {
  const y = Number(year);
  const m = Number(month);
  const d = Number(day);
  if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d)) return null;
  const parsed = new Date(Date.UTC(y, m - 1, d));
  if (
    parsed.getUTCFullYear() !== y
    || parsed.getUTCMonth() !== m - 1
    || parsed.getUTCDate() !== d
  ) {
    return null;
  }
  return { year: y, month: m, day: d };
}

export function parseSalesInvoiceDate(raw) {
  const text = String(raw ?? "").trim();
  if (!text) return null;
  let year;
  let month;
  let day;
  let match = text.match(ISO_DATE);
  if (match) {
    year = match[1];
    month = match[2];
    day = match[3];
  } else if ((match = text.match(ISO_TIMESTAMP))) {
    const hour = Number(match[4]);
    const minute = Number(match[5]);
    const second = Number(match[6]);
    if (hour > 23 || minute > 59 || second > 59) return null;
    year = match[1];
    month = match[2];
    day = match[3];
  } else if ((match = text.match(YMD_SLASH))) {
    year = match[1];
    month = match[2];
    day = match[3];
  } else if ((match = text.match(DMY_SLASH))) {
    day = match[1];
    month = match[2];
    year = match[3];
  } else if ((match = text.match(DMY_DASH))) {
    day = match[1];
    month = match[2];
    year = match[3];
  } else {
    return null;
  }
  return calendarDate(year, month, day);
}

export function salesInvoiceDateToIso(raw) {
  const parsed = parseSalesInvoiceDate(raw);
  if (!parsed) return "";
  const month = String(parsed.month).padStart(2, "0");
  const day = String(parsed.day).padStart(2, "0");
  return `${parsed.year}-${month}-${day}`;
}

export function formatSalesInvoiceDate(raw) {
  const parsed = parseSalesInvoiceDate(raw);
  if (!parsed) return "";
  const day = String(parsed.day).padStart(2, "0");
  const month = String(parsed.month).padStart(2, "0");
  return `${day}/${month}/${parsed.year}`;
}

export function addSalesInvoiceCalendarDays(raw, days) {
  const parsed = parseSalesInvoiceDate(raw);
  if (!parsed) return "";
  const term = Number(days);
  const add = Number.isFinite(term) ? Math.trunc(term) : 0;
  const utc = new Date(Date.UTC(parsed.year, parsed.month - 1, parsed.day + add));
  const month = String(utc.getUTCMonth() + 1).padStart(2, "0");
  const day = String(utc.getUTCDate()).padStart(2, "0");
  return `${utc.getUTCFullYear()}-${month}-${day}`;
}

export function readSalesInvoiceDateInput(raw) {
  const text = String(raw ?? "").trim();
  if (!text) {
    return { ok: false, iso: "", message: "Tanggal faktur wajib diisi." };
  }
  const iso = salesInvoiceDateToIso(text);
  if (!iso) {
    return { ok: false, iso: "", message: "Tanggal tidak valid. Gunakan DD/MM/YYYY." };
  }
  return { ok: true, iso, message: "" };
}
