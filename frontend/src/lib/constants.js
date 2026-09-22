// Single source of truth for workflow stages, roles, permissions, and Indonesian labels.

export const STAGES = [
  "Order Masuk",         // 0
  "Design",              // 1
  "Approval Design",     // 2
  "Payment",             // 3
  "Approval Payment",    // 4
  "Penjadwalan",         // 5
  "Screener",            // 6
  "Persiapan Helper",    // 7
  "Operator",            // 8
  "Konfirmasi Sample",   // 9
  "Cetak",               // 10
  "Packing",             // 11
  "Surat Jalan",         // 12
  "Loading",             // 13
  "Pengiriman",          // 14  (label tahap; internal key/status tetap delivery/terkirim)
];

export const ROLES = {
  admin: "Admin",
  head_production: "Head of Production",
  designer: "Designer",
  screener: "Screener",
  helper: "Helper",
  operator: "Operator",
  driver: "Driver",
  crm: "CRM",
  holding_team: "Holding",
  direktur_utama: "Direktur",
  sales_eternal: "Sales Eternal",
};

// Who is allowed to advance from a stage index (matches backend STAGE_EDIT_ROLES)
export const STAGE_EDIT_ROLES = {
  0: ["admin", "head_production"],
  1: ["admin", "designer", "head_production"],
  2: ["admin", "designer", "head_production"],
  3: ["admin", "head_production"],
  4: ["admin", "head_production"],
  5: ["admin", "head_production"],
  6: ["admin", "head_production", "screener"],
  7: ["admin", "head_production", "helper"],
  8: ["admin", "head_production", "operator"],
  9: ["admin", "head_production", "operator", "designer"],
  10: ["admin", "head_production", "operator"],
  11: ["admin", "head_production", "helper"],
  12: ["admin", "head_production"],
  13: ["admin", "head_production", "driver", "helper"],
  14: ["admin", "head_production"],  // Task 13: stage 14 = Terkirim (final). Driver hanya metadata pengantar di stage 13 Loading + mark_delivered — bukan actor advance stage.
};

export const canAdvance = (role, stageIdx) => {
  const allowed = STAGE_EDIT_ROLES[stageIdx] || [];
  return role === "admin" || allowed.includes(role);
};

const DATE_ONLY_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const ISO_DATE_PREFIX_RE = /^(\d{4})-(\d{2})-(\d{2})/;
const WIB_DATE_TIME_FORMATTER = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Jakarta",
  day: "2-digit",
  month: "2-digit",
  year: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

function _pad2(n) {
  return String(n).padStart(2, "0");
}

function _yy(year) {
  return String(year).slice(-2);
}

/** Display-only calendar date: 2026-08-26 → 26/08/26. Does not change stored values. */
export function formatDisplayDate(iso) {
  if (!iso) return "-";
  const text = String(iso).trim();
  if (!text) return "-";
  const prefix = text.match(ISO_DATE_PREFIX_RE);
  if (prefix) {
    return `${prefix[3]}/${prefix[2]}/${_yy(prefix[1])}`;
  }
  try {
    const d = new Date(text);
    if (Number.isNaN(d.getTime())) return "-";
    return `${_pad2(d.getDate())}/${_pad2(d.getMonth() + 1)}/${_yy(d.getFullYear())}`;
  } catch {
    return "-";
  }
}

/** Display-only operational timestamp in WIB: dd/mm/yy HH:mm. */
export function formatDisplayDateTime(iso) {
  if (!iso) return "-";
  const text = String(iso).trim();
  if (!text) return "-";
  if (DATE_ONLY_RE.test(text)) return formatDisplayDate(text);
  try {
    const d = new Date(text);
    if (Number.isNaN(d.getTime())) return "-";
    const parts = Object.fromEntries(
      WIB_DATE_TIME_FORMATTER.formatToParts(d).map(({ type, value }) => [type, value]),
    );
    return `${parts.day}/${parts.month}/${parts.year} ${parts.hour}:${parts.minute}`;
  } catch {
    return "-";
  }
}

export const formatDate = (iso, opts = {}) => {
  if (!iso) return "-";
  if (opts.dateOnly) return formatDisplayDate(iso);
  return formatDisplayDateTime(iso);
};

export const formatDeadline = (iso) => {
  if (!iso) return "-";
  const d = new Date(iso);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);
  const target = new Date(d);
  target.setHours(0, 0, 0, 0);
  const timePart = d.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
  if (target.getTime() === today.getTime()) return `Hari ini · ${timePart}`;
  if (target.getTime() === tomorrow.getTime()) return `Besok · ${timePart}`;
  return d.toLocaleDateString("id-ID", { weekday: "short", day: "2-digit", month: "short" });
};

export const isToday = (iso) => {
  if (!iso) return false;
  const d = new Date(iso);
  const now = new Date();
  return d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth() && d.getDate() === now.getDate();
};
