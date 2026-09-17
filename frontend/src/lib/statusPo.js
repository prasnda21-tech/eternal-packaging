import { STAGES } from "./constants";
import { dateKey } from "./shipmentStatus";
import { resolveTimelineChip } from "./timelineChip";

export const EMPTY_STATUS_PO_FILTERS = { po: "", customer: "", status: "", dateFrom: "", dateTo: "" };

export const STATUS_PO_VIEWS = {
  active: "PO Aktif",
  urgent: "PO Urgent",
  due_today: "Deadline Hari Ini",
  today: "Deadline Hari Ini",
  h1: "H-1 Pengiriman",
  h6: "Deadline ≤ 6 Jam",
  overdue: "Overdue",
  design: "Menunggu Approval Design",
  payment: "Menunggu Approval Payment",
  hold: "HOLD",
  completed: "Selesai (Terkirim)",
  work_today: "Pengerjaan Hari Ini",
  delivery_today: "Pengiriman Hari Ini",
};

export const STATUS_PO_COLUMNS = [
  "No PO",
  "Tanggal PO",
  "Pelanggan",
  "Item / Produk",
  "Status Saat Ini",
  "Umur",
  "Progress (15 Tahap)",
  "%",
  "Aksi",
];

export function statusPoViewLabel(view) {
  return STATUS_PO_VIEWS[view] || "Semua PO";
}

/** Effective workflow status from evidence — not idx < current_stage. */
export function effectivePoStatus(row) {
  if (row?.cancelled) return "Cancelled";
  if (row?.status) return row.status;
  const order = row?.order || row;
  const current = Number(order?.current_stage || 0);
  let currentLabel = null;
  for (let idx = STAGES.length - 1; idx >= 0; idx -= 1) {
    const chip = resolveTimelineChip(idx, order, current);
    if (chip.state === "current") {
      currentLabel = chip.label && chip.label !== STAGES[idx]
        ? `${STAGES[idx]} · ${chip.label}`
        : STAGES[idx];
      break;
    }
    if (!currentLabel && (chip.state === "done" || chip.state === "bypass")) {
      currentLabel = STAGES[idx];
    }
  }
  return currentLabel || order?.status_label || STAGES[current] || "—";
}

export function deliveredPercentLabel(pct) {
  const n = Number(pct);
  if (!Number.isFinite(n) || n <= 0) return "0%";
  return `${Math.round(n)}%`;
}

export function statusPoFilterOptions(rows, selected) {
  const set = new Set();
  for (const r of rows || []) {
    if (r?.status) set.add(r.status);
  }
  if (selected) set.add(selected);
  return [...set].sort((a, b) => a.localeCompare(b, "id"));
}

/** Same AND + partial text pattern as Status Pengiriman (`matchesShipFilters`). */
export function matchesStatusPoFilters(row, filters) {
  const po = (filters?.po || "").trim().toLowerCase();
  const customer = (filters?.customer || "").trim().toLowerCase();
  const status = (filters?.status || "").trim();
  const dateFrom = (filters?.dateFrom || "").trim();
  const dateTo = (filters?.dateTo || "").trim();
  if (po) {
    const hay = [
      row?.po_number,
      row?.customer,
      ...((row?.item_lines || []).map((name) => String(name || ""))),
    ].join(" ").toLowerCase();
    if (!hay.includes(po)) return false;
  }
  if (customer && !(row?.customer || "").toLowerCase().includes(customer)) return false;
  if (status && (row?.status || "") !== status) return false;
  const key = dateKey(row?.order_date);
  if (dateFrom && (!key || key < dateFrom)) return false;
  if (dateTo && (!key || key > dateTo)) return false;
  return true;
}

/** Display-only PO age from order_date. No business threshold. */
export function formatPoAgeLabel(orderDate, now = new Date()) {
  if (!orderDate) return "—";
  const text = String(orderDate).trim();
  if (!text) return "—";
  const prefix = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  const then = prefix
    ? Date.UTC(Number(prefix[1]), Number(prefix[2]) - 1, Number(prefix[3]))
    : Date.parse(text);
  if (!Number.isFinite(then)) return "—";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Jakarta",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now instanceof Date ? now : new Date(now));
  const map = Object.fromEntries(parts.map(({ type, value }) => [type, value]));
  const todayUtc = Date.UTC(Number(map.year), Number(map.month) - 1, Number(map.day));
  const thenDay = prefix ? then : Date.UTC(
    new Date(then).getUTCFullYear(),
    new Date(then).getUTCMonth(),
    new Date(then).getUTCDate(),
  );
  const days = Math.max(0, Math.round((todayUtc - thenDay) / 86400000));
  return `${days} hari`;
}

/** Display-only last-loaded timestamp: "17 Sep 2026 · 02:12". */
export function formatStatusPoUpdated(date, timeZone = "Asia/Jakarta") {
  if (!date) return "";
  const d = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(d.getTime())) return "";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone,
      day: "numeric",
      month: "numeric",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    }).formatToParts(d).map(({ type, value }) => [type, value]),
  );
  const monthIdx = Number(parts.month) - 1;
  if (!parts.day || !Number.isFinite(monthIdx) || monthIdx < 0 || !parts.year) return "";
  return `${Number(parts.day)} ${MONTHS[monthIdx]} ${parts.year} · ${parts.hour}:${parts.minute}`;
}

function _pad2(n) {
  return String(n).padStart(2, "0");
}

/** Display-only Status PO calendar date: 2026-09-17 → 17/09/2026. */
export function formatStatusPoDate(iso) {
  if (!iso) return "-";
  const text = String(iso).trim();
  if (!text) return "-";
  const prefix = text.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (prefix) return `${prefix[3]}/${prefix[2]}/${prefix[1]}`;
  const parsed = Date.parse(text);
  if (!Number.isFinite(parsed)) return "-";
  const d = new Date(parsed);
  return `${_pad2(d.getUTCDate())}/${_pad2(d.getUTCMonth() + 1)}/${d.getUTCFullYear()}`;
}

/** Parse filter date text. Accepts dd/mm/yyyy and yyyy-mm-dd. Empty → "". Invalid → null. */
export function parseStatusPoDateInput(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  const iso = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (iso) {
    const y = Number(iso[1]);
    const m = Number(iso[2]);
    const d = Number(iso[3]);
    if (m < 1 || m > 12 || d < 1 || d > 31) return null;
    return `${iso[1]}-${iso[2]}-${iso[3]}`;
  }
  const dmy = raw.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (!dmy) return null;
  const d = Number(dmy[1]);
  const m = Number(dmy[2]);
  const y = Number(dmy[3]);
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  return `${y}-${_pad2(m)}-${_pad2(d)}`;
}

/** Compact Item / Produk cell from existing item_lines names. Never invents products. */
export function itemLineSummary(lines) {
  const names = (lines || []).map((name) => String(name || "").trim()).filter(Boolean);
  if (!names.length) return { text: "-", title: undefined };
  if (names.length === 1) return { text: names[0], title: names[0] };
  return {
    text: `${names[0]} +${names.length - 1} lainnya`,
    title: names.join(" · "),
  };
}
